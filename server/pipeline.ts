/**
 * The analysis pipeline: sources → deterministic ingest → Haiku readers (in parallel) →
 * Sonnet synthesizer → normalised Profile. Every step streams AnalysisEvents so the analysis
 * screen can show readers working, signals arriving and the graph being drawn.
 */
import { randomBytes } from 'node:crypto';
import { dirname, extname } from 'node:path';
import type {
  AnalysisEvent,
  Domain,
  Person,
  Profile,
  ReaderState,
  Role,
  RoleRequirement,
  Skill,
  SourceKind,
  SourceSummary,
} from '../shared/types.ts';
import { MODELS, runStructured } from './claude.ts';
import { classifyUrl, ingestGithub, ingestNotes, ingestResume, ingestWeb } from './ingest/index.ts';
import type { IngestLog, RawSource, UploadedFile } from './ingest/types.ts';
import { READER_SYSTEM, SYNTH_SYSTEM, buildReaderPrompt, buildSynthPrompt, clip } from './prompts.ts';
import { SAMPLE_GOAL, SAMPLE_INGEST_LOGS, loadSampleSources } from './sample/index.ts';
import { ReaderOutput, SynthOutput } from './schemas.ts';
import { saveProfile, type StoredContext } from './store.ts';

export interface AnalyzeRequest {
  resume?: UploadedFile;
  links: string[];
  goal?: string;
  notes?: string;
  sample?: boolean;
}

export const MAX_LINKS = 8;

const AGENT: Record<SourceKind, string> = {
  resume: 'Resume reader',
  github: 'GitHub analyst',
  lovable: 'Lovable reviewer',
  linkedin: 'LinkedIn reader',
  portfolio: 'Link reader',
  link: 'Link reader',
  notes: 'Notes reader',
};

/** Short name used as the "who" column of the live log. */
const WHO: Record<SourceKind, string> = {
  resume: 'resume',
  github: 'github',
  lovable: 'lovable',
  linkedin: 'linkedin',
  portfolio: 'web',
  link: 'web',
  notes: 'notes',
};

const RESUME_FORMAT: Record<string, string> = { '.pdf': 'PDF', '.docx': 'DOCX', '.txt': 'TXT', '.md': 'Markdown' };

/* ───────────────────────── errors ───────────────────────── */

export const AUTH_MESSAGE = "Claude isn't signed in on the server. Run `claude login` (or set ANTHROPIC_API_KEY) and try again.";

/** Thrown after an {type:"error"} event has already been emitted. */
export class AnalysisFailed extends Error {
  readonly emitted = true;
  constructor(message: string) {
    super(message);
    this.name = 'AnalysisFailed';
  }
}

function errText(err: unknown): string {
  if (!(err instanceof Error)) return String(err ?? '');
  let s = err.message;
  const detail = (err as { detail?: unknown }).detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { result?: unknown; errors?: unknown };
    if (typeof d.result === 'string') s += ` ${d.result}`;
    if (Array.isArray(d.errors)) s += ` ${d.errors.join(' ')}`;
  }
  return s;
}

export function isAbortError(err: unknown): boolean {
  return err instanceof Error && (err.name === 'AbortError' || /\baborted\b/i.test(err.message));
}

export function isAuthError(err: unknown): boolean {
  return /\b401\b|unauthori[sz]ed|authenticat|invalid api key|api[_ ]key|\/login\b|not logged in|oauth|credential|token (?:has )?expired|expired token/i.test(
    errText(err),
  );
}

/** One short, human sentence for any failure (shown in the UI). */
export function humanizeError(err: unknown): string {
  if (isAbortError(err)) return 'Cancelled.';
  if (isAuthError(err)) return AUTH_MESSAGE;
  const m = errText(err).replace(/\s+/g, ' ').trim();
  if (/rate.?limit|\b429\b|overloaded|\b529\b/i.test(m)) return 'Claude is busy right now (rate limited or overloaded). Try again in a minute.';
  if (/ENOENT|spawn|executable not found/i.test(m)) return 'The Claude Code runtime could not be started on the server.';
  if (/failed validation|no JSON object|without a result/i.test(m)) return 'The model returned an incomplete answer. Try again.';
  if (/max_turns/i.test(m)) return 'The model ran out of turns before finishing. Try again.';
  if (/exited with code|process exited/i.test(m)) return 'Claude Code exited unexpectedly. If this keeps happening, run `claude login` on the server and retry.';
  // clipWords, not the prompt-text clip(), so UI strings never get a "…[trimmed]" marker
  return clipWords(m, 180) || 'Something went wrong.';
}

function readerErrorLine(err: unknown): string {
  if (isAuthError(err)) return 'Claude sign-in expired · skipped';
  return clipWords(humanizeError(err).replace(/\.$/, ''), 64);
}

/* ───────────────────────── small utils ───────────────────────── */

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

const tidy = (s: unknown): string => (typeof s === 'string' ? s.replace(/\s+/g, ' ').trim() : '');

/** Cut at a word boundary to at most `max` characters, with an ellipsis when cut. */
function clipWords(s: string, max: number): string {
  const t = tidy(s);
  if (t.length <= max) return t;
  const cut = t.slice(0, max - 1);
  const sp = cut.lastIndexOf(' ');
  return `${(sp > max * 0.5 ? cut.slice(0, sp) : cut).replace(/[\s,;:·-]+$/, '')}…`;
}

/** Node label: whole words up to `max` characters ("Frontend Engineering" → "Frontend"), no ellipsis unless one word is too long. */
function shortLabel(s: string, max: number): string {
  const t = tidy(s);
  if (t.length <= max) return t;
  let out = '';
  for (const w of t.split(' ')) {
    const next = out ? `${out} ${w}` : w;
    if (next.length > max) break;
    out = next;
  }
  out = out.replace(/[\s&,;:·-]+$/, '');
  return out || `${t.slice(0, max - 1)}…`;
}

function clamp(n: unknown, lo = 0, hi = 100, fallback = 0): number {
  const v = typeof n === 'number' ? n : Number(n);
  if (!Number.isFinite(v)) return fallback;
  return Math.round(Math.min(hi, Math.max(lo, v)));
}

function slug(s: string): string {
  return (
    tidy(s)
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/&/g, ' and ')
      .replace(/\+\+/g, 'pp')
      .replace(/#/g, 'sharp')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40)
      .replace(/-+$/, '') || 'item'
  );
}

/** Loose comparison key: "REST & GraphQL" ≈ "rest-graphql". */
const looseKey = (s: string) => tidy(s).toLowerCase().replace(/[^a-z0-9+#]+/g, '');

function uniqueId(base: string, used: Set<string>): string {
  let id = base;
  for (let n = 2; used.has(id); n++) id = `${base}-${n}`;
  used.add(id);
  return id;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

const urlToSrc = (url: string) => url.replace(/^https?:\/\//i, '').replace(/^www\./i, '').replace(/\/$/, '');

/** Strings from a facts value: string[] or objects with name/title/label. */
function factStrings(facts: Record<string, unknown>, keys: string[]): string[] {
  for (const k of keys) {
    const v = facts[k];
    if (!Array.isArray(v)) continue;
    const out = v
      .map((x) => {
        if (typeof x === 'string') return x;
        if (x && typeof x === 'object') {
          const o = x as Record<string, unknown>;
          return tidy(o.name ?? o.title ?? o.label ?? o.full_name ?? '');
        }
        return '';
      })
      .map((x) => tidy(x))
      .filter(Boolean);
    if (out.length) return out;
  }
  return [];
}

/** Section names the ingesters add themselves; never shown as "what the reader is reading". */
const GENERIC_HEADING =
  /^(headings|page text|profile|totals|structured data|ui copy|routes|backend hints|external services|libraries|notes on this data|forks|other original|recent public activity|technologies mentioned|top repositories|profile readme|github:)/i;

/** Page headings ("- h2: Checkout, rebuilt" lines from the web ingester) or markdown sub-headings. */
function contentHeadings(content: string): string[] {
  const page = [...content.matchAll(/^- h[1-3]: (.+)$/gm)].map((m) => tidy(m[1]));
  const md = [...content.matchAll(/^#{2,3}\s+(?:\d+\.\s+)?(.+)$/gm)].map((m) => tidy(m[1])).filter((h) => !GENERIC_HEADING.test(h));
  return [...page, ...md]
    .map((h) => h.replace(/^["“]|["”]$/g, ''))
    .filter((h, i, all) => h.length > 2 && h.length < 70 && all.indexOf(h) === i)
    .slice(0, 10);
}

/** "TypeScript · 17 repos" → { label: "TypeScript", detail: "17 repos" }. */
function splitSignal(s: string, fallback: string): { label: string; detail: string } {
  const i = s.indexOf(' · ');
  return i > 0 ? { label: clipWords(s.slice(0, i), 32), detail: clipWords(s.slice(i + 3), 40) } : { label: clipWords(s, 32), detail: fallback };
}

/* ───────────────────────── planning ───────────────────────── */

export interface Planned {
  id: string;
  kind: SourceKind;
  label: string;
  kindLabel: string;
  src: string;
  url?: string;
  /** Deterministic fetch/extract step; must not throw for expected failures. */
  ingest: (log: IngestLog, signal: AbortSignal) => Promise<RawSource>;
}

function idFor(kind: SourceKind, n: number): string {
  if (kind === 'github' || kind === 'linkedin') return n === 1 ? kind : `${kind}-${n}`;
  return `${kind}-${n}`;
}

/** Classify, dedupe and cap the user's links. Exported so the HTTP layer can validate input. */
export function usableLinks(links: string[]) {
  const seen = new Set<string>();
  const out: NonNullable<ReturnType<typeof classifyUrl>>[] = [];
  for (const raw of links) {
    if (out.length >= MAX_LINKS) break;
    if (typeof raw !== 'string' || !raw.trim()) continue;
    const c = classifyUrl(raw);
    if (!c) continue;
    const k = c.url.toLowerCase().replace(/\/$/, '');
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(c);
  }
  return out;
}

function planSources(input: AnalyzeRequest): Planned[] {
  if (input.sample) {
    return loadSampleSources().map((raw, i) => ({
      id: raw.id,
      kind: raw.kind,
      label: raw.label,
      kindLabel: raw.kindLabel,
      src: raw.src,
      url: raw.url,
      ingest: async (log, signal) => {
        // Replay plausible fetch logs so the demo feels like the live product.
        await sleep(180 + i * 260, signal);
        for (const line of SAMPLE_INGEST_LOGS[raw.id] ?? []) {
          await sleep(300 + Math.round(Math.random() * 400), signal);
          log(line);
        }
        return raw;
      },
    }));
  }

  const planned: Planned[] = [];
  if (input.resume) {
    const file = input.resume;
    const fmt = RESUME_FORMAT[extname(file.originalName).toLowerCase()];
    planned.push({
      id: 'resume',
      kind: 'resume',
      label: 'Resume',
      kindLabel: fmt ? `Resume · ${fmt}` : 'Resume',
      src: file.originalName || 'resume',
      ingest: (log, signal) => ingestResume(file, log, signal),
    });
  }

  const counters: Partial<Record<SourceKind, number>> = {};
  for (const link of usableLinks(input.links)) {
    const n = (counters[link.kind] = (counters[link.kind] ?? 0) + 1);
    planned.push({
      id: idFor(link.kind, n),
      kind: link.kind,
      label: link.label,
      kindLabel: link.kindLabel,
      src: urlToSrc(link.url),
      url: link.url,
      ingest: (log, signal) =>
        link.kind === 'github' ? ingestGithub(link.url, log, signal) : ingestWeb(link.url, link.kind, link.label, link.kindLabel, log, signal),
    });
  }

  const notes = input.notes?.trim();
  if (notes) {
    planned.push({
      id: 'notes',
      kind: 'notes',
      label: 'Notes',
      kindLabel: 'Extra context',
      src: 'Pasted context',
      ingest: async () => ingestNotes(notes),
    });
  }
  return planned;
}

/* ───────────────────────── readers ───────────────────────── */

export interface ReadResult {
  planned: Planned;
  raw?: RawSource;
  reader?: ReaderOutput;
  error?: string;
  aiError?: unknown;
}

/**
 * Readers run with extended thinking switched off.
 *
 * Under Claude Code, Haiku 4.5 runs with a large default thinking budget (`effort` does not apply to it):
 * measured on the sample GitHub source, a reader took ~130 s, ~10k of it thinking tokens, versus ~23 s
 * (and a third of the cost) without thinking. Readers do extraction, not reasoning, so thinking is off.
 * Set TAYYARI_READER_THINKING=1 to keep the default behaviour.
 */
const READER_THINKING = process.env.TAYYARI_READER_THINKING === '1' ? undefined : ({ type: 'disabled' } as const);

/** Throttled emitter for one reader's state (progress updates arrive per token). */
function readerChannel(state: ReaderState, emit: (e: AnalysisEvent) => void) {
  let last = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;
  const flush = () => {
    timer = null;
    if (closed) return;
    last = Date.now();
    emit({ type: 'reader', reader: { ...state } });
  };
  return {
    state,
    update(patch: Partial<ReaderState>, force = false) {
      Object.assign(state, patch);
      if (closed) return;
      const wait = 260 - (Date.now() - last);
      if (force || wait <= 0) {
        if (timer) clearTimeout(timer);
        flush();
      } else if (!timer) {
        timer = setTimeout(flush, wait);
      }
    },
    close() {
      if (timer) clearTimeout(timer);
      timer = null;
      closed = true;
    },
  };
}

/** Rotating, specific activity lines shown while a reader works. */
function activityLines(raw: RawSource): string[] {
  const facts = raw.facts ?? {};
  const heads = factStrings(facts, ['headings', 'sections', 'caseStudies', 'pages', 'titles', 'projects']);
  const headings = heads.length ? heads : contentHeadings(raw.content);
  const lines: string[] = [];
  switch (raw.kind) {
    case 'github': {
      const repos = factStrings(facts, ['topRepos', 'repos', 'repositories', 'pinned']);
      const langs = factStrings(facts, ['languages']);
      const verbs = ['Reading README', 'Scanning commits', 'Checking tests and CI', 'Reading package.json'];
      repos.slice(0, 6).forEach((r, i) => lines.push(`${verbs[i % verbs.length]} · ${r}`));
      if (langs.length) lines.splice(2, 0, `Mapping ${plural(langs.length, 'language')} across repos`);
      if (!lines.length) lines.push('Reading repositories', 'Scanning READMEs', 'Checking tests and CI');
      break;
    }
    case 'resume': {
      headings.slice(0, 5).forEach((h) => lines.push(`Reading ${h.toLowerCase()} · ${clipWords(raw.src, 28)}`));
      lines.push('Weighing impact in each bullet', 'Separating listed skills from used ones');
      break;
    }
    case 'lovable': {
      const routes = factStrings(facts, ['routes']).filter((r) => r !== '/');
      routes.slice(0, 2).forEach((r) => lines.push(`Opening route · ${clipWords(r, 30)}`));
      headings.slice(0, 4).forEach((h) => lines.push(`Walking through · ${clipWords(h, 32)}`));
      lines.push('Checking auth and data wiring', 'Reviewing the AI features');
      break;
    }
    case 'portfolio': {
      headings.slice(0, 5).forEach((h) => lines.push(`Parsing case study: “${clipWords(h, 30)}”`));
      lines.push('Looking for outcomes and numbers');
      break;
    }
    case 'linkedin':
      lines.push('Reading headline and about', 'Reading experience entries', 'Checking featured work');
      break;
    case 'notes':
      lines.push('Reading your notes', 'Separating claims from goals');
      break;
    default: {
      headings.slice(0, 4).forEach((h) => lines.push(`Reading · ${clipWords(h, 34)}`));
      lines.push(`Extracting evidence · ${clipWords(raw.src, 30)}`);
    }
  }
  return lines.length ? lines : [`Reading ${clipWords(raw.src, 34)}`];
}

function cleanReader(r: ReaderOutput): ReaderOutput {
  const person = r.person
    ? {
        ...(tidy(r.person.name) ? { name: tidy(r.person.name) } : {}),
        ...(tidy(r.person.currentRole) ? { currentRole: tidy(r.person.currentRole) } : {}),
        ...(typeof r.person.yearsExperience === 'number' && Number.isFinite(r.person.yearsExperience) && r.person.yearsExperience >= 0
          ? { yearsExperience: Math.round(r.person.yearsExperience * 10) / 10 }
          : {}),
        ...(tidy(r.person.location) ? { location: tidy(r.person.location) } : {}),
      }
    : undefined;
  return {
    sub: clipWords(r.sub, 48),
    summary: tidy(r.summary),
    ...(person && Object.keys(person).length ? { person } : {}),
    signals: (r.signals ?? [])
      .map((s) => ({ label: clipWords(s.label, 32), detail: clipWords(s.detail, 40) }))
      .filter((s) => s.label)
      .slice(0, 12),
    skills: (r.skills ?? [])
      .map((s) => ({ name: clipWords(s.name, 40), evidence: clipWords(s.evidence, 160), strength: clamp(s.strength, 1, 5, 2) }))
      .filter((s) => s.name)
      .slice(0, 25),
    notes: (r.notes ?? []).map((n) => clipWords(n, 140)).filter(Boolean).slice(0, 4),
  };
}

function quickDetail(raw: RawSource): string {
  if (raw.kind === 'resume') return 'resume';
  if (raw.kind === 'portfolio') return 'portfolio';
  if (raw.kind === 'notes') return 'notes';
  return raw.label;
}

/** Crisp result line, e.g. "6 roles · 4 projects · 23 skills extracted" or "34 repos · 9 languages · 18 skills extracted". */
function resultLine(out: ReaderOutput): string {
  const parts = out.sub
    .split(/\s*·\s*/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.some((p) => /skill/i.test(p))) return `${parts.slice(0, 3).join(' · ')} extracted`;
  return [...parts.slice(0, 2), `${plural(out.skills.length, 'skill')} extracted`].join(' · ');
}

/* ───────────────────────── main entry ───────────────────────── */

export async function runAnalysis(input: AnalyzeRequest, emit: (e: AnalysisEvent) => void, signal: AbortSignal): Promise<Profile> {
  const send = (e: AnalysisEvent) => {
    if (!signal.aborted) emit(e);
  };
  const fail = (message: string): never => {
    send({ type: 'error', message });
    throw new AnalysisFailed(message);
  };

  const goal = tidy(input.goal) || (input.sample ? SAMPLE_GOAL : '') || undefined;
  const planned = planSources(input);
  if (!planned.length) fail('Add a resume, a link or some notes to analyse.');

  /* (b) announce the readers */
  const states = new Map<string, ReaderState>(
    planned.map((p) => [
      p.id,
      { sourceId: p.id, kind: p.kind, src: p.src, kindLabel: p.kindLabel, agent: AGENT[p.kind], status: 'queued', line: 'Queued', progress: 0 },
    ]),
  );
  send({ type: 'readers', readers: [...states.values()].map((s) => ({ ...s })) });
  send({ type: 'phase', phase: 'reading', label: 'Haiku readers in progress' });

  /* (c) read every source concurrently */
  // Signals stream in one at a time (~150ms apart), deduped across all sources so the cloud stays clean.
  const signalStreams: Promise<void>[] = [];
  const seen = new Set<string>();
  const streamSignals = (items: Array<{ label: string; detail: string }>, kind: SourceKind, after: Promise<void> = Promise.resolve()) => {
    const p = (async () => {
      await after;
      for (const it of items) {
        const k = looseKey(it.label);
        if (!k || seen.has(k)) continue;
        seen.add(k);
        send({ type: 'signal', label: it.label, src: it.detail, kind });
        await sleep(150, signal);
      }
    })().catch(() => undefined);
    signalStreams.push(p);
    return p;
  };

  const readOne = async (p: Planned): Promise<ReadResult> => {
    const ch = readerChannel(states.get(p.id)!, send);
    const who = WHO[p.kind];
    let quick: Promise<void> = Promise.resolve();
    let raw: RawSource | undefined;
    try {
      ch.update({ status: 'reading', line: `Opening ${clipWords(p.src, 36)}`, progress: 6 }, true);

      /* deterministic ingest */
      try {
        raw = await p.ingest((text) => {
          if (signal.aborted) return;
          send({ type: 'log', who, text, kind: p.kind });
          ch.update({ line: clipWords(text, 70), progress: Math.min(35, Math.max(10, ch.state.progress + 7)) });
        }, signal);
      } catch (err) {
        if (signal.aborted) throw err;
        console.error(`[ingest ${p.id}]`, err);
        raw = {
          id: p.id,
          kind: p.kind,
          label: p.label,
          url: p.url,
          src: p.src,
          kindLabel: p.kindLabel,
          content: '',
          facts: {},
          quickSignals: [],
          error: err instanceof Error ? err.message : String(err),
        };
      }
      signal.throwIfAborted();
      raw = { ...raw, id: p.id, url: raw.url ?? p.url };
      ch.update({ kindLabel: raw.kindLabel || p.kindLabel, src: raw.src || p.src });

      if (raw.error) {
        const line = clipWords((tidy(raw.error).split(/(?<=\.)\s/)[0] ?? raw.error).replace(/\.$/, ''), 64);
        ch.update({ status: 'error', line }, true);
        send({ type: 'log', who, text: `Could not read ${raw.label}: ${clipWords(raw.error, 90)}`, kind: p.kind });
        return { planned: p, raw, error: raw.error };
      }

      ch.update({ progress: Math.max(ch.state.progress, 35) });
      quick = streamSignals(
        (raw.quickSignals ?? []).map((s) => splitSignal(s, quickDetail(raw!))),
        p.kind,
      );

      /* Haiku reader */
      const lines = activityLines(raw);
      let li = 0;
      ch.update({ line: lines[0] }, true);
      const ticker = setInterval(() => {
        li = (li + 1) % lines.length;
        // never lower progress (onProgress may already have pushed it past 90)
        ch.update({ line: lines[li], progress: ch.state.progress < 90 ? ch.state.progress + 1 : ch.state.progress });
      }, 1400);
      let out: ReaderOutput;
      try {
        const res = await runStructured({
          model: MODELS.reader,
          system: READER_SYSTEM(raw!.kind),
          prompt: buildReaderPrompt(raw!),
          schema: ReaderOutput,
          tools: raw!.filePath ? ['Read'] : undefined,
          dirs: raw!.filePath ? [dirname(raw!.filePath)] : undefined,
          effort: 'low',
          thinking: READER_THINKING,
          signal,
          onProgress: (chars) => {
            const pr = 35 + Math.round(57 * (1 - Math.exp(-chars / 1800)));
            if (pr > ch.state.progress) ch.update({ progress: pr });
          },
        });
        out = cleanReader(res.data);
      } finally {
        clearInterval(ticker);
      }

      ch.update({ status: 'done', progress: 100, line: resultLine(out) }, true);
      send({ type: 'log', who, text: `${raw.label} read · ${plural(out.skills.length, 'skill')}, ${plural(out.signals.length, 'signal')}`, kind: p.kind });
      if (out.notes[0]) send({ type: 'log', who, text: clipWords(out.notes[0], 90), kind: p.kind });
      void streamSignals(
        out.signals.map((s) => ({ label: s.label, detail: s.detail || quickDetail(raw!) })),
        p.kind,
        quick,
      );
      return { planned: p, raw, reader: out };
    } catch (err) {
      if (signal.aborted) throw err;
      console.error(`[reader ${p.id}]`, err);
      const line = readerErrorLine(err);
      ch.update({ status: 'error', line }, true);
      send({ type: 'log', who, text: `Reader failed: ${line}`, kind: p.kind });
      return { planned: p, raw, error: humanizeError(err), aiError: err };
    } finally {
      ch.close();
    }
  };

  const results = await Promise.all(planned.map(readOne));
  signal.throwIfAborted();

  const ok = results.filter((r) => r.reader && r.raw);
  if (!ok.length) {
    const aiErrors = results.filter((r) => r.aiError !== undefined);
    if (aiErrors.some((r) => isAuthError(r.aiError))) fail(AUTH_MESSAGE);
    if (aiErrors.length) fail(`The readers couldn't reach Claude: ${humanizeError(aiErrors[0]!.aiError)}`);
    fail(
      results.length === 1 && results[0]!.error
        ? `We couldn't read that source: ${results[0]!.error}`
        : "We couldn't read any of your sources. Check the file and links, then try again.",
    );
  }

  /* (d) synthesize */
  send({ type: 'phase', phase: 'synthesizing', label: 'Sonnet is drawing your graph' });
  send({ type: 'synth', step: 1, chars: 0 });
  send({ type: 'log', who: 'sonnet', text: `Merging evidence across ${plural(ok.length, 'source')}`, kind: 'sonnet' });

  const resumeRaw = results.find((r) => r.planned.kind === 'resume')?.raw;
  const notesRaw = results.find((r) => r.planned.kind === 'notes')?.raw;
  const prompt = buildSynthPrompt({
    goal,
    sources: results.map((r) => ({
      id: r.planned.id,
      label: r.raw?.label ?? r.planned.label,
      kind: r.planned.kind,
      url: r.raw?.url ?? r.planned.url,
      ok: !!r.reader,
      error: r.error,
      facts: r.raw?.facts,
      reader: r.reader,
    })),
    resumeText: resumeRaw && !resumeRaw.error && !resumeRaw.filePath ? resumeRaw.content : undefined,
    notesText: notesRaw?.content,
  });

  let synth: SynthOutput;
  try {
    synth = await synthesize(prompt, send, signal);
  } catch (err) {
    if (signal.aborted) throw err;
    console.error('[synth]', err);
    return fail(`Couldn't build your graph: ${humanizeError(err)}`);
  }

  /* (e) normalise into a valid Profile */
  const profile = toProfile(synth, results, goal, !!input.sample);
  send({
    type: 'log',
    who: 'sonnet',
    text: `Graph ready · ${plural(profile.domains.length, 'domain')}, ${plural(profile.skills.length, 'skill')}, ${plural(profile.roles.length, 'role')}`,
    kind: 'sonnet',
  });

  /* (f) persist and finish */
  const context: StoredContext = {
    sources: results.map((r) => ({
      id: r.planned.id,
      label: r.raw?.label ?? r.planned.label,
      kind: r.planned.kind,
      url: r.raw?.url ?? r.planned.url,
      content: r.raw && !r.raw.error ? r.raw.content : '',
    })),
    readers: Object.fromEntries(ok.map((r) => [r.planned.id, r.reader!])),
  };
  try {
    await saveProfile(profile, context);
  } catch (err) {
    console.error('[store] could not save profile', err); // still cached in memory
  }
  await Promise.allSettled(signalStreams);
  send({ type: 'synth', step: 3 });
  send({ type: 'phase', phase: 'done', label: 'Your graph is ready' });
  send({ type: 'done', profile });
  return profile;
}

/* ───────────────────────── synthesis ───────────────────────── */

async function synthesize(prompt: string, send: (e: AnalysisEvent) => void, signal: AbortSignal): Promise<SynthOutput> {
  let step: 1 | 2 | 3 = 1;
  let lastEmit = 0;
  let lastChars = -1;
  let offset = 0; // a retry restarts the SDK's count; keep the UI's counter monotonic
  const onProgress = (generated: number) => {
    const chars = offset + generated;
    if (chars === lastChars) return; // thinking deltas carry no visible characters
    lastChars = chars;
    if (step < 2 && chars >= 2500) {
      step = 2;
      send({ type: 'synth', step, chars });
      send({ type: 'log', who: 'sonnet', text: 'Scoring domains and skills', kind: 'sonnet' });
      lastEmit = Date.now();
    } else if (step < 3 && chars >= 6500) {
      step = 3;
      send({ type: 'synth', step, chars });
      send({ type: 'log', who: 'sonnet', text: 'Ranking next roles by alignment', kind: 'sonnet' });
      lastEmit = Date.now();
    } else if (Date.now() - lastEmit > 300) {
      lastEmit = Date.now();
      send({ type: 'synth', step, chars });
    }
  };

  const attempt = async (repair?: string): Promise<SynthOutput> => {
    const res = await runStructured({
      model: MODELS.synth,
      system: SYNTH_SYSTEM,
      prompt: repair ? `${prompt}\n\n${repair}` : prompt,
      schema: SynthOutput,
      effort: 'medium',
      maxTurns: 4,
      signal,
      onProgress,
    });
    const d = res.data;
    if (d.domains.length < 2 || d.skills.length < 4 || d.roles.length < 1) {
      throw new Error(`structured output failed validation: graph too small (${d.domains.length} domains, ${d.skills.length} skills, ${d.roles.length} roles)`);
    }
    return d;
  };

  try {
    return await attempt();
  } catch (err) {
    if (signal.aborted || isAuthError(err)) throw err;
    console.error('[synth] first attempt failed, retrying once:', err instanceof Error ? err.message : err);
    send({ type: 'log', who: 'sonnet', text: 'Tightening the graph, one more pass', kind: 'sonnet' });
    offset = Math.max(0, lastChars);
    const reason = clip((err instanceof Error ? err.message : String(err)).replace(/\s+/g, ' '), 300);
    return attempt(
      `IMPORTANT — your previous attempt failed (${reason}). Return ONLY the structured output, valid against the schema: ` +
        'every required field present, scores as integers 0–100, kebab-case ids, evidence.sourceId taken from the list above, ' +
        '4–7 domains, 12–26 skills, 4–6 roles with 4–7 requirements each.',
    );
  }
}

/* ───────────────────────── normalisation ───────────────────────── */

export function toProfile(synth: SynthOutput, results: ReadResult[], goal: string | undefined, sample: boolean): Profile {
  const okIds = results.filter((r) => r.reader).map((r) => r.planned);
  const used = new Set<string>(['you', ...results.map((r) => r.planned.id)]);

  /* source id resolution: exact, case-insensitive, label, kind */
  const resolveSource = (raw: string): string | undefined => {
    const s = tidy(raw);
    if (!s) return undefined;
    const lower = s.toLowerCase();
    const exact = okIds.find((p) => p.id === s) ?? okIds.find((p) => p.id.toLowerCase() === lower);
    if (exact) return exact.id;
    const byLabel = okIds.filter((p) => p.label.toLowerCase() === lower || looseKey(p.label) === looseKey(s));
    if (byLabel.length === 1) return byLabel[0]!.id;
    const byKind = okIds.filter((p) => p.kind === lower);
    if (byKind.length === 1) return byKind[0]!.id;
    return undefined;
  };

  /* roles first (titles are needed for skill.unlocks) */
  const rawRoles = [...synth.roles]
    .filter((r) => tidy(r.title))
    .sort((a, b) => clamp(b.alignment) - clamp(a.alignment))
    .filter((r, i, arr) => arr.findIndex((x) => looseKey(x.title) === looseKey(r.title)) === i)
    .slice(0, 6);
  const roleTitles = rawRoles.map((r) => clipWords(r.title, 30));
  const matchRole = (t: string): string | undefined => {
    const k = looseKey(t);
    if (!k) return undefined;
    return roleTitles.find((title) => looseKey(title) === k) ?? roleTitles.find((title) => looseKey(title).includes(k) || k.includes(looseKey(title)));
  };

  /* domains */
  const domainAlias = new Map<string, string>();
  const domains: Domain[] = [];
  for (const d of synth.domains) {
    if (domains.length >= 7) break;
    const name = tidy(d.name) || tidy(d.short);
    if (!name || domains.some((x) => looseKey(x.name) === looseKey(name))) continue;
    const id = uniqueId(slug(d.id || name), used);
    for (const alias of [d.id, slug(d.id), name, d.short]) if (tidy(alias)) domainAlias.set(looseKey(alias), id);
    domains.push({
      id,
      name: clipWords(name, 40),
      short: shortLabel(tidy(d.short) || name, 16),
      score: clamp(d.score),
      summary: tidy(d.summary),
    });
  }

  if (!domains.length) {
    const id = uniqueId('core', used);
    domains.push({ id, name: 'Core skills', short: 'Core', score: clamp(synth.mastery.score), summary: '' });
  }

  /* skills */
  const skillAlias = new Map<string, string>();
  let skills: Skill[] = [];
  for (const s of synth.skills) {
    if (skills.length >= 28) break;
    const name = tidy(s.name);
    if (!name || skills.some((x) => looseKey(x.name) === looseKey(name))) continue;
    const id = uniqueId(slug(s.id || name), used);
    for (const alias of [s.id, name]) if (tidy(alias)) skillAlias.set(looseKey(alias), id);
    const dk = looseKey(s.domainId);
    const domainId =
      domainAlias.get(dk) ??
      [...domainAlias.entries()].find(([k]) => (dk.length >= 4 && k.includes(dk)) || (k.length >= 4 && dk.includes(k)))?.[1] ??
      domains[0]!.id;
    const evidence = (s.evidence ?? [])
      .map((e) => ({ sourceId: resolveSource(e.sourceId) ?? '', text: clipWords(e.text, 120) }))
      .filter((e, i, arr) => e.sourceId && e.text && arr.findIndex((x) => x.text === e.text) === i)
      .slice(0, 3);
    const unlocks = [...new Set((s.unlocks ?? []).map((u) => matchRole(u) ?? clipWords(u, 30)).filter(Boolean))].slice(0, 3);
    skills.push({ id, name: clipWords(name, 24), domainId, score: clamp(s.score), evidence, unlocks });
  }

  // Drop domains nobody points at (keep at least two), then group skills by domain order.
  const populated = domains.filter((d) => skills.some((s) => s.domainId === d.id));
  const finalDomains = populated.length >= 2 ? populated : domains;
  const order = new Map(finalDomains.map((d, i) => [d.id, i]));
  skills = skills
    .map((s) => (order.has(s.domainId) ? s : { ...s, domainId: finalDomains[0]!.id }))
    .map((s, i) => ({ s, i }))
    .sort((a, b) => order.get(a.s.domainId)! - order.get(b.s.domainId)! || a.i - b.i)
    .map(({ s }) => s);

  const skillById = new Map(skills.map((s) => [s.id, s]));
  const resolveSkill = (raw: string | undefined, name: string): string | undefined => {
    if (raw && skillById.has(raw)) return raw;
    for (const cand of [raw, name]) {
      if (!tidy(cand)) continue;
      const k = looseKey(cand!);
      const hit = skillAlias.get(k) ?? skills.find((s) => looseKey(s.name) === k || looseKey(s.name) === k.replace(/s$/, ''))?.id;
      if (hit) return hit;
    }
    return undefined;
  };

  /* roles */
  const roles: Role[] = rawRoles.map((r, ri) => {
    const requirements: RoleRequirement[] = [];
    for (const q of r.requirements ?? []) {
      if (requirements.length >= 7) break;
      const name = tidy(q.name);
      if (!name) continue;
      const skillId = resolveSkill(q.skillId, name);
      const skill = skillId ? skillById.get(skillId) : undefined;
      const label = clipWords(skill?.name ?? name, 28);
      // dedupe on the resolved identity: two paraphrases of one skill must not both appear
      if (requirements.some((x) => (skillId && x.skillId === skillId) || looseKey(x.name) === looseKey(label))) continue;
      const current = skill ? skill.score : clamp(q.current);
      let target = clamp(q.target, 0, 100, 70);
      const have = q.have ? current >= 55 : current >= target;
      if (!have && target <= current) target = Math.min(100, current + 10);
      requirements.push({ name: label, ...(skillId ? { skillId } : {}), have, current, target });
    }
    return {
      id: uniqueId(slug(r.id || r.title), used),
      title: roleTitles[ri]!,
      alignment: clamp(r.alignment),
      eta: tidy(r.eta) || '~6 months',
      why: clipWords(r.why, 200),
      requirements,
    };
  });

  /* sources */
  const synthSources = new Map<string, SynthOutput['sources'][number]>();
  for (const s of synth.sources ?? []) {
    const id = resolveSource(s.id);
    if (id && !synthSources.has(id)) synthSources.set(id, s);
  }
  const sources: SourceSummary[] = results.map((r) => {
    const p = r.planned;
    const ss = synthSources.get(p.id);
    const notes = (ss?.notes?.length ? ss.notes : (r.reader?.notes ?? [])).map((n) => clipWords(n, 140)).filter(Boolean).slice(0, 4);
    return {
      id: p.id,
      kind: p.kind,
      label: r.raw?.label || p.label,
      ...((r.raw?.url ?? p.url) ? { url: r.raw?.url ?? p.url } : {}),
      sub: clipWords(tidy(ss?.sub) || r.reader?.sub || fallbackSub(r), 48),
      summary: r.reader?.summary ?? (r.error ? `Could not be read: ${r.error}` : ''),
      notes,
      ok: !!r.reader,
      ...(!r.reader && r.error ? { error: r.error } : {}),
    };
  });

  /* person */
  const readerPeople = [...results]
    .sort((a, b) => (a.planned.kind === 'resume' ? -1 : 0) - (b.planned.kind === 'resume' ? -1 : 0))
    .map((r) => r.reader?.person)
    .filter((x): x is NonNullable<ReaderOutput['person']> => !!x);
  const rp = (k: 'name' | 'currentRole' | 'location') => readerPeople.find((x) => tidy(x[k]))?.[k];
  const sp = synth.person;
  const synthName = tidy(sp.name);
  const name = synthName && !/^you$/i.test(synthName) ? synthName : tidy(rp('name')) || 'You';
  const currentRole = tidy(sp.currentRole) || tidy(rp('currentRole')) || 'Professional';
  const location = tidy(sp.location) || tidy(rp('location')) || undefined;
  const yrsRaw = typeof sp.yearsExperience === 'number' ? sp.yearsExperience : readerPeople.find((x) => typeof x.yearsExperience === 'number')?.yearsExperience;
  const yearsExperience = typeof yrsRaw === 'number' && Number.isFinite(yrsRaw) && yrsRaw >= 0 ? Math.round(yrsRaw * 10) / 10 : undefined;
  const headline =
    tidy(sp.headline) ||
    [currentRole, yearsExperience !== undefined ? `${yearsExperience} yrs` : '', location?.split(',')[0]?.trim() ?? ''].filter(Boolean).join(' · ');
  const person: Person = {
    name,
    headline: clipWords(headline, 80),
    currentRole: clipWords(currentRole, 40),
    ...(location ? { location } : {}),
    ...(yearsExperience !== undefined ? { yearsExperience } : {}),
    summary: tidy(sp.summary),
  };

  return {
    id: `${sample ? 'sample-' : ''}${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`,
    createdAt: new Date().toISOString(),
    ...(goal ? { goal } : {}),
    person,
    mastery: { score: clamp(synth.mastery.score), insight: clipWords(synth.mastery.insight, 140) },
    sources,
    domains: finalDomains,
    skills,
    roles,
    models: { reader: MODELS.reader, synth: MODELS.synth },
  };
}

function fallbackSub(r: ReadResult): string {
  const f = r.raw?.facts ?? {};
  const num = (k: string) => (typeof f[k] === 'number' ? (f[k] as number) : undefined);
  const parts: string[] = [];
  const repos = num('repos') ?? num('publicRepos');
  if (repos !== undefined) parts.push(plural(repos, 'repo'));
  const langs = Array.isArray(f.languages) ? f.languages.length : undefined;
  if (langs) parts.push(plural(langs, 'language'));
  const pages = num('pages');
  if (!parts.length && pages !== undefined) parts.push(plural(pages, 'page'));
  const cases = num('caseStudies');
  if (cases !== undefined) parts.push(plural(cases, 'case study').replace('studys', 'studies'));
  if (parts.length) return parts.join(' · ');
  if (!r.reader && r.error) return 'Could not be read';
  return r.raw?.kindLabel || r.planned.kindLabel;
}
