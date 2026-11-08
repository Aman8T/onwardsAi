/**
 * Live analysis, calm theme: one Haiku reader per source streams into the Sonnet synthesizer.
 * Thin progress rings, plain text and hairlines; driven entirely by `useApp(s => s.analysis)`.
 * Board: CalmAnalysis. No glow, no loops: every motion here is a one-shot opacity / transform.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import type { ReaderState, ReaderStatus, SourceKind } from '../../shared/types';
import { actions, useApp, type AnalysisState, type LogLine, type Signal } from '../store';
import { health } from '../lib/api';
import { EASE, useTween } from '../lib/ui';
import { Brand } from '../components/Brand';
import { Ring } from '../components/Ring';
import '../styles/analysis.css';

const READER_MODEL = 'claude-haiku-4-5';
const SYNTH_MODEL = 'claude-sonnet-5-5';
const AUTO_OPEN_MS = 1600;

const STEPS = ['Merge evidence', 'Score domains', 'Rank roles'] as const;
const STEP_LEGEND = ['Merge', 'Score', 'Rank'] as const;
const STEP_STATE_LABEL = { done: 'done', active: 'in progress', todo: 'queued' } as const;

const STATUS_LABEL: Record<RowStatus, string> = { queued: 'Queued', reading: 'Reading', done: 'Done', error: 'Failed', stopped: 'Stopped' };
const DEFAULT_LINE: Record<RowStatus, string> = {
  queued: 'Waiting for a free reader',
  reading: 'Opening source',
  done: 'Finished reading',
  error: 'Could not read this source',
  stopped: 'Stopped before finishing',
};

const SOURCE_NAME: Record<SourceKind, string> = {
  resume: 'Resume',
  github: 'GitHub',
  lovable: 'Lovable',
  portfolio: 'Portfolio',
  linkedin: 'LinkedIn',
  link: 'Link',
  notes: 'Notes',
};

const NUMBER_WORD = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
const AUTH_RE = /auth|sign(?:ed)?[\s-]?in|log(?:ged)?[\s-]?in|credential|unauthori[sz]ed|\b401\b|api[\s_-]?key/i;

type SynthState = 'wait' | 'build' | 'done' | 'error';
/** A reader that never finished because the run failed is shown as stopped, not as still reading. */
type RowStatus = ReaderStatus | 'stopped';

const clamp = (n: number, lo = 0, hi = 100) => Math.min(hi, Math.max(lo, n));

/* ───────────────────────── derived numbers ───────────────────────── */

function summarize(a: AnalysisState) {
  const total = a.readers.length;
  const finished = a.readers.filter((r) => r.status === 'done').length;
  const failed = a.readers.filter((r) => r.status === 'error').length;
  const pending = total - finished - failed;
  // a failed reader will not progress any further, so it counts as settled
  const avg = total
    ? a.readers.reduce((n, r) => n + (r.status === 'done' || r.status === 'error' ? 100 : clamp(r.progress)), 0) / total
    : 0;
  const raw = avg * 0.7 + (clamp(a.synthStep, 0, 3) / 3) * 100 * 0.3;
  const overall = a.phase === 'done' ? 100 : Math.min(99, Math.round(raw));
  const state: SynthState = a.phase === 'error' ? 'error' : a.phase === 'done' ? 'done' : a.phase === 'synthesizing' ? 'build' : 'wait';
  return { total, finished, failed, pending, overall, state };
}

/** Live mm:ss since `startedAt`; freezes the moment the run stops. */
function useElapsed(startedAt: number, running: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running, startedAt]);
  const s = Math.max(0, Math.floor((now - startedAt) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/* ───────────────────────── page ───────────────────────── */

export function Analysis() {
  const a = useApp((s) => s.analysis);
  const reduce = useReducedMotion();
  const d = useMemo(() => summarize(a), [a]);
  const done = a.phase === 'done';
  const failed = a.phase === 'error';
  const running = !done && !failed;
  const elapsed = useElapsed(a.startedAt, running);
  const overallShown = useTween(d.overall, { duration: 700 });

  // a short pause on the finished state, then straight into the graph
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => actions.showGraph(), AUTO_OPEN_MS);
    return () => clearTimeout(t);
  }, [done]);

  // when a run fails, find out whether the server can offer the pre-computed sample
  const [hasSample, setHasSample] = useState(false);
  useEffect(() => {
    if (!failed) return;
    let alive = true;
    void health().then((h) => {
      if (alive && h?.hasSample) setHasSample(true);
    });
    return () => {
      alive = false;
    };
  }, [failed]);

  const mode = done ? 'done' : failed ? 'error' : 'run';
  const rowStatus = (r: ReaderState): RowStatus => (failed && (r.status === 'queued' || r.status === 'reading') ? 'stopped' : r.status);
  const phaseLabel =
    a.phaseLabel ||
    (done
      ? 'Your graph is ready'
      : failed
        ? 'Analysis failed'
        : a.phase === 'synthesizing'
          ? 'Sonnet is drawing your graph'
          : a.phase === 'reading'
            ? 'Haiku readers in progress'
            : 'Starting readers');

  const plural = d.total === 1 ? '' : 's';
  const sub = failed
    ? 'The run stopped before your graph was ready. Nothing was saved.'
    : done
      ? `Sonnet merged ${d.total} source${plural} into one graph. Opening it now.`
      : d.total === 0
        ? 'Spinning up one Haiku reader per source. Your graph appears the moment the synthesizer finishes.'
        : a.phase === 'synthesizing' || d.pending === 0
          ? `${d.finished} of ${d.total} source${plural} read. Sonnet is merging the evidence into your graph.`
          : `${NUMBER_WORD[d.total] ?? d.total} reader${d.total === 1 ? ' is' : 's are'} working in parallel. Your graph appears the moment the synthesizer finishes.`;

  const announce = done
    ? 'Your graph is ready.'
    : failed
      ? 'The analysis failed.'
      : `${phaseLabel}. ${d.finished} of ${d.total} sources read.`;

  const rows = a.readers.map((r) => ({ r, status: rowStatus(r) }));

  return (
    <main className="an-root" data-state={mode}>
      <div className="sr-only" role="status" aria-live="polite">
        {announce}
      </div>

      <header className="an-header">
        <div className="an-brand">
          <Brand onClick={() => actions.cancelAnalysis()} />
        </div>
        <nav className="an-nav" aria-label="Analysis actions">
          <button type="button" className="btn btn-quiet an-cancel" onClick={() => actions.cancelAnalysis()}>
            Cancel
          </button>
        </nav>
      </header>

      <section className="an-title">
        <div className="an-title-l">
          <span className="kicker an-kicker" data-state={mode}>
            {done ? 'Complete' : failed ? 'Stopped' : 'Analysing'} · <span className="numeral">{elapsed}</span>
          </span>
          <h1 className="serif an-h1">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={mode}
                className="an-h1-in"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.25, ease: EASE }}
              >
                {mode === 'done' ? 'Your graph is ready' : mode === 'error' ? 'Something got in the way' : 'Reading your sources'}
              </motion.span>
            </AnimatePresence>
          </h1>
          <p className="an-sub">{sub}</p>
        </div>

        <div className="an-overall">
          <Ring value={d.overall} size={44} role="progressbar" label="Overall progress" />
          <div className="an-overall-txt">
            <span className="numeral an-pct">{overallShown}%</span>
            <span className="kicker an-overall-note">
              <span className="an-overall-count">{d.total ? `${d.finished} of ${d.total} source${plural} read` : 'Preparing sources'}</span>
              {d.failed > 0 && <span className="an-overall-failed"> · {d.failed} failed</span>}
              <span className="an-overall-phase"> · {phaseLabel}</span>
            </span>
          </div>
        </div>
      </section>

      <section className="panel an-pipe" aria-label="Analysis pipeline">
        <div className="an-pipe-main">
          <div className="an-cols" aria-hidden="true">
            <span className="kicker">Source</span>
            <span className="kicker">Reader</span>
            <span className="kicker an-cols-r">Status</span>
          </div>
          <ul className="an-rows" aria-label="Source readers">
            {rows.length > 0
              ? rows.map(({ r, status }, i) => <ReaderRow key={r.sourceId} reader={r} status={status} index={i} reduce={!!reduce} />)
              : [0, 1, 2, 3].map((i) => <SkeletonRow key={i} index={i} reduce={!!reduce} />)}
          </ul>
        </div>

        <SynthCard
          state={d.state}
          a={a}
          pending={d.pending}
          total={d.total}
          canSample={a.sample || hasSample}
          onOpen={() => actions.showGraph()}
        />
      </section>

      <div className="an-bottom">
        <SignalsPanel signals={a.signals} readers={a.readers} />
        <AgentLog logs={a.logs} ended={done || failed} />
      </div>
    </main>
  );
}

/* ───────────────────────── reader rows ───────────────────────── */

function ReaderRow({ reader: r, status, index, reduce }: { reader: ReaderState; status: RowStatus; index: number; reduce: boolean }) {
  const pct =
    status === 'done'
      ? 100
      : status === 'queued'
        ? 0
        : status === 'stopped'
          ? clamp(Math.round(r.progress))
          : Math.max(status === 'error' ? 8 : 4, clamp(Math.round(r.progress)));
  const line = r.line || DEFAULT_LINE[status];
  return (
    <motion.li
      className="an-row hairline"
      data-status={status}
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06, duration: 0.3, ease: EASE }}
    >
      <div className="an-src">
        <span className="an-src-name">{r.kindLabel}</span>
        <span className="an-src-sub" title={r.src}>
          {r.src}
        </span>
      </div>

      <div className="an-reader">
        <span className="an-ring">
          <Ring value={pct} size={28} role="progressbar" label={`${r.agent} progress`} />
          <svg className="an-check" viewBox="0 0 28 28" width="28" height="28" aria-hidden="true" focusable="false">
            <path d="M9.6 14.3L12.6 17.1L18.3 10.9" />
          </svg>
        </span>
        <div className="an-reader-txt">
          <span className="kicker an-agent">
            {r.agent} · {READER_MODEL}
          </span>
          <span className="an-line" title={line}>
            {line}
          </span>
        </div>
      </div>

      <span className="an-stat" data-status={status}>
        {STATUS_LABEL[status]}
      </span>
    </motion.li>
  );
}

function SkeletonRow({ index, reduce }: { index: number; reduce: boolean }) {
  return (
    <motion.li
      className="an-row hairline"
      data-status="queued"
      aria-hidden="true"
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.06, duration: 0.3, ease: EASE }}
    >
      <div className="an-src">
        <span className="an-sk an-sk-a" style={{ width: '44%' }} />
        <span className="an-sk an-sk-b" style={{ width: '68%' }} />
      </div>
      <div className="an-reader">
        <span className="an-ring">
          <Ring value={0} size={28} />
        </span>
        <div className="an-reader-txt">
          <span className="an-sk an-sk-b" style={{ width: 132 }} />
          <span className="an-sk an-sk-a" style={{ width: `${58 + ((index * 13) % 30)}%` }} />
        </div>
      </div>
      <span className="an-sk an-sk-a an-stat-sk" />
    </motion.li>
  );
}

/* ───────────────────────── synthesizer ───────────────────────── */

/** The 56px ring with the step fraction in serif inside. */
function SynthRing({ value, frac, label }: { value: number; frac: string; label: string }) {
  return (
    <div className="an-synth-ring">
      <Ring value={value} size={56} role="img" label={label} />
      <span className="numeral an-frac" aria-hidden="true">
        {frac}
      </span>
    </div>
  );
}

/** Characters written so far, counting up rather than jumping. */
function Chars({ value, done }: { value: number; done: boolean }) {
  const shown = useTween(value, { duration: 700 });
  return (
    <span className="kicker an-chars">
      {done ? 'Written' : 'Writing'} <span className="numeral">{shown.toLocaleString('en-US')}</span> chars
    </span>
  );
}

function SynthCard({
  state,
  a,
  pending,
  total,
  canSample,
  onOpen,
}: {
  state: SynthState;
  a: AnalysisState;
  pending: number;
  total: number;
  canSample: boolean;
  onOpen: () => void;
}) {
  const title =
    state === 'done'
      ? 'Graph ready'
      : state === 'build'
        ? 'Building your graph'
        : total === 0
          ? 'Waiting on readers'
          : pending > 0
            ? `Waiting on ${pending} reader${pending === 1 ? '' : 's'}`
            : 'Handing over to Sonnet';

  // the step Sonnet is on: 0 while waiting, 1 merge, 2 score, 3 rank, then done
  const step = state === 'done' ? 3 : state === 'build' ? clamp(a.synthStep, 1, 3) : 0;
  const activeIdx = state === 'build' ? step - 1 : -1;
  const stepState = (i: number) => (state === 'done' || i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'todo');
  // the ring fills with synthStep/3, but only the finish closes it
  const value = state === 'done' ? 100 : state === 'build' ? Math.min(step / 3, 0.94) * 100 : 0;
  const ready = state === 'done';
  const ringLabel =
    state === 'done'
      ? 'Sonnet synthesizer, finished'
      : state === 'build'
        ? `Sonnet synthesizer, step ${step} of 3: ${STEP_LEGEND[step - 1].toLowerCase()}`
        : pending > 0
          ? `Sonnet synthesizer, waiting on ${pending} reader${pending === 1 ? '' : 's'}`
          : 'Sonnet synthesizer, waiting';

  return (
    <div className="an-synth hairline" data-state={state}>
      <AnimatePresence mode="wait" initial={false}>
        {state === 'error' ? (
          <motion.div
            key="error"
            className="an-err"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            <SynthError message={a.error ?? 'The analysis failed.'} canSample={canSample} step={clamp(a.synthStep, 0, 3)} />
          </motion.div>
        ) : (
          <motion.div
            key="main"
            className="an-synth-in"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            <SynthRing value={value} frac={`${step}/3`} label={ringLabel} />

            <div className="an-synth-mid">
              <div className="an-synth-head">
                <span className="kicker an-synth-model">{SYNTH_MODEL} · synthesizer</span>
                <h2 className="title an-synth-title">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.span
                      key={state}
                      className="an-synth-title-in"
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -6 }}
                      transition={{ duration: 0.2, ease: EASE }}
                    >
                      {title}
                    </motion.span>
                  </AnimatePresence>
                </h2>
              </div>

              <ol className="an-steps" aria-label="Synthesizer steps">
                {STEPS.map((label, i) => {
                  const st = stepState(i);
                  return (
                    <li key={label} className="an-step" data-state={st} aria-current={st === 'active' ? 'step' : undefined}>
                      <span className="an-dot" aria-hidden="true" />
                      <span className="an-step-label">{label}</span>
                      <span className="an-step-state">{STEP_STATE_LABEL[st]}</span>
                    </li>
                  );
                })}
              </ol>
            </div>

            <div className="an-cta-wrap">
              {a.synthChars > 0 && <Chars value={a.synthChars} done={state === 'done'} />}
              <button type="button" className="btn btn-primary an-open" data-ready={ready} disabled={!ready} onClick={onOpen}>
                <span>Open my skill graph</span>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true" focusable="false">
                  <path d="M2.5 7h9M7.5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <span className="kicker an-auto" data-on={ready} aria-hidden="true">
                Opening automatically
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SynthError({ message, canSample, step }: { message: string; canSample: boolean; step: number }) {
  const auth = AUTH_RE.test(message);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const openSample = async () => {
    setBusy(true);
    setNote(null);
    try {
      await actions.loadSampleProfile();
    } catch (err) {
      setNote(err instanceof Error ? err.message : 'The sample could not be loaded.');
      setBusy(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText('claude auth login');
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable: the command is still on screen */
    }
  };

  return (
    <>
      <div className="an-err-head">
        <SynthRing value={(step / 3) * 100} frac={`${step}/3`} label="Sonnet synthesizer, stopped" />
        <div className="an-synth-head">
          <span className="kicker an-synth-model">{SYNTH_MODEL} · synthesizer</span>
          <h2 className="title an-synth-title">Analysis stopped</h2>
        </div>
      </div>
      <p className="an-err-msg" role="alert">
        {message}
      </p>
      {auth && (
        <div className="an-hint">
          <span>Claude Code is not signed in on the server. Run:</span>
          <span className="an-hint-row">
            <code>claude auth login</code>
            <button type="button" className="btn btn-quiet btn-sm an-copy" onClick={copy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </span>
        </div>
      )}
      <div className="an-err-actions">
        <button type="button" className="btn btn-primary" onClick={() => actions.startOver()}>
          Try again
        </button>
        {canSample && (
          <button type="button" className="btn" onClick={openSample} disabled={busy} aria-busy={busy}>
            {busy ? 'Opening sample' : 'Open the sample graph'}
          </button>
        )}
      </div>
      {note && <p className="an-err-note">{note}</p>}
    </>
  );
}

/* ───────────────────────── signals + log ───────────────────────── */

function SignalsPanel({ signals, readers }: { signals: Signal[]; readers: ReaderState[] }) {
  // one legend entry per source kind that is being read (or has produced a signal)
  const legend = useMemo(() => {
    const kinds: SourceKind[] = [];
    for (const k of [...readers.map((r) => r.kind), ...signals.map((s) => s.kind)]) if (!kinds.includes(k)) kinds.push(k);
    return kinds.map((k) => ({ kind: k, name: SOURCE_NAME[k] ?? k, count: signals.filter((s) => s.kind === k).length }));
  }, [readers, signals]);

  // chips: one per distinct label, with how many times a reader spotted it (first sighting keeps its place)
  const chips = useMemo(() => {
    const byLabel = new Map<string, { key: string; label: string; count: number; srcs: string[] }>();
    for (const s of signals) {
      const key = s.label.trim().toLowerCase();
      const hit = byLabel.get(key);
      if (hit) {
        hit.count += 1;
        if (!hit.srcs.includes(s.src)) hit.srcs.push(s.src);
      } else {
        byLabel.set(key, { key, label: s.label, count: 1, srcs: [s.src] });
      }
    }
    return [...byLabel.values()];
  }, [signals]);

  return (
    <section className="an-signals">
      <h2 className="kicker an-h2">Signals</h2>
      {signals.length === 0 ? (
        <div aria-hidden="true">
          <div className="an-chips">
            {[96, 132, 84, 118, 104, 76].map((w, i) => (
              <span key={i} className="an-sk an-sk-chip" style={{ width: w }} />
            ))}
          </div>
          <p className="kicker an-empty">Skills and facts show up here the moment a reader spots them.</p>
        </div>
      ) : (
        <ul className="an-chips" aria-label="Signals found so far">
          {chips.map((c) => (
            <li key={c.key} className="chip an-sig" title={c.srcs.join(' · ')}>
              <span className="an-sig-label">{c.label}</span>
              <span className="numeral">{c.count}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="kicker an-legend">
        {legend.map((k) => (
          <Fragment key={k.kind}>
            <span className="an-leg">
              {k.name} <span className="numeral">{k.count}</span>
            </span>
            <span aria-hidden="true"> · </span>
          </Fragment>
        ))}
        <span className="an-total">
          <span className="numeral">{signals.length}</span> signal{signals.length === 1 ? '' : 's'}
        </span>
      </p>
    </section>
  );
}

function AgentLog({ logs, ended }: { logs: LogLine[]; ended: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // newest line at the bottom; follow it unless the reader has scrolled up
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [logs.length]);

  return (
    <section className="an-log">
      <div className="an-log-head">
        <h2 className="kicker an-h2">Agent log</h2>
        <span className="kicker an-log-count">
          <span className="numeral">{logs.length}</span> event{logs.length === 1 ? '' : 's'}
        </span>
      </div>
      <div
        ref={box}
        className="an-logbox scroll"
        role="log"
        aria-label="Agent log"
        aria-live="off"
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {logs.length === 0 ? (
          <p className="kicker an-empty">{ended ? 'No agent output was recorded' : 'Waiting for the first agent to report'}</p>
        ) : (
          logs.map((l) => (
            <div key={l.id} className="an-ll">
              <span className="numeral an-ll-t">{l.t}</span>
              <span className="an-ll-who">{l.who}</span>
              <span className="an-ll-text" title={l.text}>
                {l.text}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
