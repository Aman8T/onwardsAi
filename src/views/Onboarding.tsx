/**
 * Home as a dashboard (board "Home as a dashboard"). Header, a left-aligned greeting row, then a 12-column grid:
 * the compact "Build your skill graph" form (span 5), "Your graph" (the person's own top roles, or a small preview
 * of the sample; span 4), tiles into the other tools (span 3), "How it works" in four columns (span 7) and the
 * recent posting matches kept on this device (span 5). Under 900px every item takes the full width.
 *
 * The ambient field is mounted once in index.html and the cursor light lives in App; this view paints neither.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ClipboardEvent, FormEvent, KeyboardEvent } from 'react';
import { AnimatePresence, motion, useAnimate, useReducedMotion } from 'motion/react';
import type { Profile, SourceKind } from '../../shared/types';
import { AccountMenu } from '../auth/AccountMenu';
import { Brand } from '../components/Brand';
import { SchemeToggle } from '../components/SchemeToggle';
import { Radar } from '../components/Radar';
import type { RadarAxis } from '../components/Radar';
import { IconArrowRight, IconDoc, IconMinus, IconPlus, IconUpload } from '../components/Icons';
import { SiteFooter } from '../components/SiteFooter';
import * as api from '../lib/api';
import { EASE, NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { useHandoffMessage } from '../lib/useHandoff';
import { BAND_LABEL } from '../match/model';
import { readRecent } from '../match/recent';
import { actions, useApp } from '../store';
import '../styles/onboarding.css';

/* ───────────────────────── constants ───────────────────────── */

const MAX_BYTES = 10 * 1024 * 1024;
const MAX_LINKS = 10;
const ACCEPT = '.pdf,.docx,.txt,.md';
const FILE_TYPES: Record<string, string> = { pdf: 'PDF', docx: 'DOCX', txt: 'TXT', md: 'Markdown' };

/* ───────────────────────── resume file rules ───────────────────────── */

function extOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i + 1).toLowerCase();
}

function formatBytes(b: number): string {
  if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(b / 1024))} KB`;
}

/** Returns an error message, or null when the file is acceptable. */
function validateFile(f: File): string | null {
  const ext = extOf(f.name);
  if (ext === 'doc') return 'Legacy .doc files are not supported. Save it as DOCX or PDF and try again.';
  if (!(ext in FILE_TYPES)) return 'That file type is not supported. Use a PDF, DOCX, TXT or MD resume.';
  if (f.size === 0) return 'That file looks empty. Pick a different one.';
  if (f.size > MAX_BYTES) return `That file is ${formatBytes(f.size)}. Resumes must be 10 MB or smaller.`;
  return null;
}

/* ───────────────────────── link rules (mirror the server) ───────────────────────── */

interface LinkItem {
  /** Dedupe key. */
  key: string;
  /** What is sent to the server. */
  href: string;
  /** What the row shows: host + path, no protocol. */
  display: string;
  kind: SourceKind;
  /** Chip text: the source label, or the host for a generic link. */
  tag: string;
  /** What kind of page it is (Profile, Repo, Live app...); shown as the chip's tooltip. */
  meta: string;
}

const HOST_RE = /^(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*$/i;

function classify(host: string, segs: string[]): Pick<LinkItem, 'kind' | 'tag' | 'meta'> {
  if (host === 'github.com') {
    return { kind: 'github', tag: 'GitHub', meta: segs.length === 0 ? 'Home' : segs.length === 1 ? 'Profile' : 'Repo' };
  }
  if (host === 'lovable.app' || host.endsWith('.lovable.app')) return { kind: 'lovable', tag: 'Lovable', meta: 'Live app' };
  if (host === 'lovable.dev' || host.endsWith('.lovable.dev')) return { kind: 'lovable', tag: 'Lovable', meta: 'Project' };
  if (host === 'linkedin.com' || host.endsWith('.linkedin.com')) {
    return { kind: 'linkedin', tag: 'LinkedIn', meta: segs[0] === 'in' ? 'Profile' : 'Page' };
  }
  if (
    host.endsWith('.github.io') ||
    host.endsWith('.vercel.app') ||
    host.endsWith('.netlify.app') ||
    /(^|\.)(behance|dribbble|framer)\./.test(host)
  ) {
    return { kind: 'portfolio', tag: 'Portfolio', meta: 'Site' };
  }
  return { kind: 'link', tag: host, meta: 'Link' };
}

function parseLink(token: string): LinkItem | null {
  let t = token
    .trim()
    .replace(/^[<("'[]+/, '')
    .replace(/[>)"'\],;.]+$/, '');
  if (!t) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(t)) t = `https://${t}`;
  let u: URL;
  try {
    u = new URL(t);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  if (u.username || u.password) return null; // never carry credentials along
  const host = u.hostname.toLowerCase().replace(/^www\./, '');
  if (!HOST_RE.test(host)) return null;
  const path = u.pathname.replace(/\/+$/, '');
  const segs = path.split('/').filter(Boolean);
  return {
    key: `${host}${path}${u.search}`.toLowerCase(),
    href: `${u.origin}${path}${u.search}${u.hash}`,
    display: `${host}${path}${u.search}`,
    ...classify(host, segs),
  };
}

interface MergeResult {
  links: LinkItem[];
  added: LinkItem[];
  /** Tokens that could not be read as links (left in the input so they can be fixed). */
  rejected: string[];
  error: string | null;
}

/** Split pasted text on whitespace/commas, validate, dedupe and append. */
function mergeLinks(existing: LinkItem[], raw: string): MergeResult {
  const tokens = raw.split(/[\s,]+/).filter(Boolean);
  const links = [...existing];
  const added: LinkItem[] = [];
  const invalid: string[] = [];
  const overflowed: string[] = [];
  let dupes = 0;
  for (const tok of tokens) {
    const item = parseLink(tok);
    if (!item) {
      invalid.push(tok);
    } else if (links.some((l) => l.key === item.key)) {
      dupes++;
    } else if (links.length >= MAX_LINKS) {
      overflowed.push(tok);
    } else {
      links.push(item);
      added.push(item);
    }
  }

  const clip = (s: string) => (s.length > 38 ? `${s.slice(0, 37)}…` : s);
  const errors: string[] = [];
  if (overflowed.length) errors.push(`You can connect up to ${MAX_LINKS} links.`);
  if (invalid.length === 1) {
    errors.push(`"${clip(invalid[0])}" does not look like a link. Try something like github.com/you.`);
  } else if (invalid.length > 1) {
    const sample = invalid.slice(0, 2).map((s) => `"${clip(s)}"`).join(', ');
    errors.push(`${invalid.length} entries do not look like links (${sample}${invalid.length > 2 ? ', …' : ''}).`);
  }
  if (dupes && !added.length && !errors.length) errors.push(dupes === 1 ? 'That link is already added.' : 'Those links are already added.');

  return { links, added, rejected: [...invalid, ...overflowed], error: errors.length ? errors.join(' ') : null };
}

const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');

/* ───────────────────────── static content ───────────────────────── */

const STEPS = [
  {
    n: '01 · Connect',
    title: 'Share what you have',
    body: 'Drop in a resume and add GitHub, Lovable, a portfolio or any public link. Each source adds evidence.',
  },
  {
    n: '02 · Analyse',
    title: 'One reader per source',
    body: 'A separate reader works through each source, then a final pass reconciles them into one graph with the evidence behind every score.',
  },
  {
    n: '03 · Explore',
    title: 'Two graphs, one chat',
    body: 'See where you stand today and the roles you could grow into next. Ask the chat what the gaps mean and what to build.',
  },
  {
    n: '04 · Apply',
    title: 'Match, practise, prepare',
    body: 'Paste a posting to see every requirement against your evidence, rehearse the interview, and plan the weeks to a role.',
  },
];

const PREP_NOTE = 'Needs your graph first';

/* ───────────────────────── Resume dropzone ───────────────────────── */

function ResumeDrop({
  file,
  error,
  onPick,
  onRemove,
}: {
  file: File | null;
  error: string | null;
  onPick: (files: FileList | File[]) => void;
  onRemove: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [armed, setArmed] = useState(false); // a file is being dragged somewhere over the page
  const [over, setOver] = useState(false); // ...and it is over the zone itself

  // Dropping a file anywhere on the page picks the resume (and never navigates away to the file).
  useEffect(() => {
    let depth = 0;
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth++;
      setArmed(true);
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) {
        setArmed(false);
        setOver(false);
      }
    };
    const dragover = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setArmed(false);
      setOver(false);
      const files = e.dataTransfer?.files;
      if (files && files.length) onPick(files);
    };
    const end = () => {
      depth = 0;
      setArmed(false);
      setOver(false);
    };
    window.addEventListener('dragenter', enter);
    window.addEventListener('dragleave', leave);
    window.addEventListener('dragover', dragover);
    window.addEventListener('drop', drop);
    window.addEventListener('dragend', end);
    return () => {
      window.removeEventListener('dragenter', enter);
      window.removeEventListener('dragleave', leave);
      window.removeEventListener('dragover', dragover);
      window.removeEventListener('drop', drop);
      window.removeEventListener('dragend', end);
    };
  }, [onPick]);

  const browse = () => input.current?.click();
  const cls = `ob-drop${file ? ' has-file' : ''}${armed ? ' is-armed' : ''}${over ? ' is-over' : ''}`;

  return (
    <div className="ob-field">
      <span className="kicker ob-label" id="ob-resume-label">
        Resume
      </span>
      <div
        className={cls}
        role="group"
        aria-labelledby="ob-resume-label"
        onDragEnter={(e) => hasFiles(e.nativeEvent) && setOver(true)}
        onDragOver={(e) => hasFiles(e.nativeEvent) && setOver(true)}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
        }}
      >
        {file ? (
          <>
            <button type="button" className="ob-drop-main" onClick={browse} aria-label={`Replace resume ${file.name}`}>
              <IconDoc className="ob-drop-icon" size={22} strokeWidth={1.5} />
              <span className="ob-drop-text">
                <span className="ob-file-name">{file.name}</span>
                <span className="kicker ob-file-meta">
                  {FILE_TYPES[extOf(file.name)] ?? 'File'} · {formatBytes(file.size)} · drop to replace
                </span>
              </span>
              <span className="chip ob-ready">Ready</span>
            </button>
            <button type="button" className="btn btn-quiet btn-sm ob-drop-x" aria-label="Remove resume" onClick={onRemove}>
              Remove
            </button>
          </>
        ) : (
          <button type="button" className="ob-drop-main ob-drop-empty" onClick={browse} aria-describedby="ob-resume-hint">
            <IconUpload className="ob-drop-icon" size={22} strokeWidth={1.5} />
            <span className="ob-drop-label">
              {over ? (
                'Release to add your resume'
              ) : (
                <>
                  Drop your resume here or <span className="ob-browse">browse</span>
                </>
              )}
            </span>
            <span className="kicker ob-file-meta" id="ob-resume-hint">
              PDF, DOCX, TXT or MD · up to 10 MB
            </span>
          </button>
        )}
        <input
          ref={input}
          type="file"
          className="sr-only"
          accept={ACCEPT}
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            if (e.target.files?.length) onPick(e.target.files);
            e.target.value = ''; // let the same file be picked again
          }}
        />
      </div>
      {error && (
        <p className="ob-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/* ───────────────────────── View ───────────────────────── */

export function Onboarding() {
  const reduce = useReducedMotion();
  const narrow = useMediaQuery(NARROW_QUERY);

  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [links, setLinks] = useState<LinkItem[]>([]);
  const [linkInput, setLinkInput] = useState('');
  const [linkError, setLinkError] = useState<string | null>(null);
  const [goal, setGoal] = useState('');
  const [notes, setNotes] = useState('');
  const [notesOpen, setNotesOpen] = useState(false);
  const [announce, setAnnounce] = useState('');

  const [hasSample, setHasSample] = useState(false);
  const [sampleBusy, setSampleBusy] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);

  const [hintScope, animateHint] = useAnimate();

  const profile = useApp((st) => st.profile);
  const handoffMessage = useHandoffMessage();
  const recent = useMemo(() => readRecent().slice(0, 4), []);
  const [sample, setSample] = useState<Profile | null>(null);

  // The pre-computed sample is only offered when the server actually has one.
  useEffect(() => {
    let live = true;
    void api.health().then((h) => {
      if (live) setHasSample(!!h?.hasSample);
    });
    return () => {
      live = false;
    };
  }, []);

  // The same pre-computed sample "Open the sample graph" opens, read once for the preview (no AI call).
  useEffect(() => {
    if (!hasSample || profile) return;
    let live = true;
    api
      .sampleProfile()
      .then((p) => {
        if (live) setSample(p);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [hasSample, profile]);

  const shown = profile ?? sample;
  const axes = useMemo<RadarAxis[]>(
    () => (shown ? shown.domains.map((d) => ({ id: d.id, label: d.short || d.name, value: d.score })) : []),
    [shown],
  );
  const topRoles = useMemo(() => (shown ? [...shown.roles].sort((a, b) => b.alignment - a.alignment).slice(0, 3) : []), [shown]);

  const pickFile = useCallback((files: FileList | File[]) => {
    const f = files[0];
    if (!f) return;
    const err = validateFile(f);
    if (err) {
      setFileError(err);
      return;
    }
    setFileError(null);
    setFile(f);
    setAnnounce(`Resume added: ${f.name}`);
  }, []);

  const removeFile = () => {
    setFile(null);
    setFileError(null);
    setAnnounce('Resume removed');
  };

  /** Validate + add whatever is in `raw`. Returns the merge so callers can react synchronously. */
  const commitLinks = (raw: string): MergeResult => {
    const res = mergeLinks(links, raw);
    setLinks(res.links);
    setLinkError(res.error);
    if (res.added.length) setAnnounce(`Added ${res.added.map((l) => l.display).join(', ')}`);
    return res;
  };

  const addFromInput = () => {
    if (!linkInput.trim()) return;
    const res = commitLinks(linkInput);
    // keep only what could not be read, so it can be fixed in place
    setLinkInput(res.rejected.join(' '));
  };

  const onLinkKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter' || e.nativeEvent.isComposing) return;
    e.preventDefault(); // Enter adds the link; it never submits the whole form
    addFromInput();
  };

  const onLinkPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text');
    if (/[\s,]/.test(text.trim())) {
      // several links at once: add them all
      e.preventDefault();
      commitLinks(text);
    }
  };

  const removeLink = (l: LinkItem) => {
    setLinks((cur) => cur.filter((x) => x.key !== l.key));
    setLinkError(null);
    setAnnounce(`Removed ${l.display}`);
  };

  // a link typed but not yet added counts too, as long as it can actually be read
  const pendingLink = useMemo(() => linkInput.split(/[\s,]+/).some((t) => t && parseLink(t)), [linkInput]);
  const ready = !!file || links.length > 0 || pendingLink || notes.trim().length > 0;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready) {
      // a small sideways nudge on the hint (transform only; skipped under reduced motion)
      if (!reduce && hintScope.current) void animateHint(hintScope.current, { x: [0, -5, 5, -3, 3, 0] }, { duration: 0.3 });
      return;
    }
    let finalLinks = links;
    if (linkInput.trim()) {
      // someone pasted a link and went straight for the big button: count it
      const res = mergeLinks(links, linkInput);
      setLinks(res.links);
      finalLinks = res.links;
      if (res.rejected.length) {
        setLinkInput(res.rejected.join(' '));
        setLinkError(res.error);
        return;
      }
      setLinkInput('');
      setLinkError(null);
    }
    void actions.startAnalysis({
      file,
      links: finalLinks.map((l) => l.href),
      goal: goal.trim() || undefined,
      notes: notes.trim() || undefined,
    });
  };

  const openSample = async () => {
    setSampleBusy(true);
    setSampleError(null);
    try {
      await actions.loadSampleProfile();
    } catch (err) {
      setSampleError(err instanceof Error ? err.message : 'Could not open the sample profile.');
      setSampleBusy(false);
    }
  };

  const scrollToTop = () => window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  const focusForm = () => {
    document.getElementById('ob-card-title')?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    document.getElementById('ob-link')?.focus({ preventScroll: true });
  };

  return (
    <div className="ob-root" id="top">
      <div className="wrap ob-wrap">
        <header className="ob-header">
          <span className="ob-brand">
            <Brand tagline={!narrow} onClick={scrollToTop} />
          </span>
          <nav className="ob-nav" aria-label="Primary">
            <a className="btn btn-quiet" href="/how-it-works">
              How it works
            </a>
            <SchemeToggle />
            <AccountMenu />
          </nav>
        </header>

        <main className="ob-main">
          <motion.div
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
          >
            <div className="ob-greet">
              <h1 className="ob-h1 serif">
                <span className="ob-h1-line">See where you stand.</span> <span className="ob-h1-line em">Then where you could go.</span>
              </h1>
              <p className="kicker ob-reads">
                <span className="ob-reads-label">Reads from</span>
                <span className="ob-reads-list">Resume · GitHub · Lovable · Portfolio · any link</span>
              </p>
            </div>

            <div className="ob-grid">
              <div className="ob-cell ob-c-form">
                <form className="panel ob-form" onSubmit={onSubmit} aria-labelledby="ob-card-title" noValidate>
            <div className="ob-card-head">
              <h2 id="ob-card-title" className="ob-card-title serif">
                Build your skill graph
              </h2>
              <p className="ob-card-sub">The more you connect, the sharper it gets.</p>
            </div>

            {handoffMessage && (
              <p className="ob-error ob-handoff" role="alert">
                {handoffMessage}
              </p>
            )}

            <ResumeDrop file={file} error={fileError} onPick={pickFile} onRemove={removeFile} />

            {/* links */}
            <div className="ob-field ob-field-links">
              <span className="kicker ob-label" id="ob-links-label">
                Links
              </span>
              <div>
                <ul className="ob-links" aria-labelledby="ob-links-label">
                  <AnimatePresence initial={false}>
                    {links.map((l) => (
                      <motion.li
                        key={l.key}
                        className="ob-li"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2, ease: EASE }}
                      >
                        <div className="ob-link-row">
                          <span className="chip ob-tag" title={`${l.tag} · ${l.meta}`}>
                            {/* a generic link's tag is its host, which the URL beside it already shows */}
                            <span className="ob-tag-text">{l.kind === 'link' ? l.meta : l.tag}</span>
                          </span>
                          <span className="ob-url" title={l.href}>
                            {l.display}
                          </span>
                          <button type="button" className="btn btn-quiet btn-sm ob-remove" aria-label={`Remove ${l.display}`} onClick={() => removeLink(l)}>
                            Remove
                          </button>
                        </div>
                        {l.kind === 'linkedin' && (
                          <p className="ob-note">LinkedIn usually blocks automated reading. Paste your About text under extra context to be safe.</p>
                        )}
                      </motion.li>
                    ))}
                  </AnimatePresence>
                </ul>

                <div className="ob-add-row">
                  <label htmlFor="ob-link" className="sr-only">
                    Add a link
                  </label>
                  <input
                    id="ob-link"
                    className="field"
                    type="text"
                    inputMode="url"
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                    spellCheck={false}
                    placeholder="Paste a GitHub, Lovable, LinkedIn or portfolio link"
                    value={linkInput}
                    aria-invalid={linkError ? true : undefined}
                    aria-describedby={linkError ? 'ob-link-error' : undefined}
                    onChange={(e) => {
                      setLinkInput(e.target.value);
                      if (linkError) setLinkError(null);
                    }}
                    onKeyDown={onLinkKey}
                    onPaste={onLinkPaste}
                  />
                  <button type="button" className="btn ob-add" disabled={!linkInput.trim()} onClick={addFromInput} aria-label="Add link">
                    Add
                  </button>
                </div>
                {linkError && (
                  <p id="ob-link-error" className="ob-error ob-links-error" role="alert">
                    {linkError}
                  </p>
                )}
              </div>
            </div>

            {/* goal */}
            <div className="ob-field">
              <label htmlFor="ob-goal" className="kicker ob-label">
                Goal
              </label>
              <input
                id="ob-goal"
                className="field"
                type="text"
                maxLength={200}
                autoComplete="off"
                placeholder="Where do you want to be in 12 months? (optional)"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
              />
            </div>

            {/* extra context */}
            <div className="ob-field ob-field-more">
              <button
                type="button"
                className="btn btn-quiet ob-more"
                aria-expanded={notesOpen}
                aria-controls="ob-notes-panel"
                onClick={() => setNotesOpen((o) => !o)}
              >
                {notesOpen ? <IconMinus size={14} strokeWidth={1.75} /> : <IconPlus size={14} strokeWidth={1.75} />}
                Add extra context
                {!notesOpen && notes.trim() && <span className="chip ob-more-badge">Added</span>}
              </button>
              <AnimatePresence initial={false}>
                {notesOpen && (
                  <motion.div
                    key="notes"
                    id="ob-notes-panel"
                    className="ob-collapse"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2, ease: EASE }}
                  >
                    <label htmlFor="ob-notes" className="sr-only">
                      Extra context
                    </label>
                    <textarea
                      id="ob-notes"
                      className="field"
                      rows={4}
                      maxLength={8000}
                      placeholder="Your LinkedIn About section, achievements, certifications, anything the links cannot show…"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                    />
                    {notes.length > 6000 && <span className="numeral ob-count">{notes.length.toLocaleString()} / 8,000</span>}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* submit */}
            <div className="ob-submit">
              <button type="submit" className="btn btn-primary ob-cta" aria-disabled={!ready} aria-describedby={ready ? undefined : 'ob-hint'}>
                Build my skill graph
                <IconArrowRight size={16} strokeWidth={1.75} />
              </button>

              <AnimatePresence initial={false}>
                {!ready && (
                  <motion.p
                    key="hint"
                    id="ob-hint"
                    className="ob-hint"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2, ease: EASE }}
                  >
                    <span ref={hintScope} className="ob-hint-text">
                      Add a resume, a link or a few lines of context to begin.
                    </span>
                  </motion.p>
                )}
              </AnimatePresence>

              <button type="button" className="btn btn-quiet ob-sample-btn" onClick={() => void actions.startAnalysis({ sample: true, links: [] })}>
                Try a sample profile
              </button>
              <p className="kicker ob-fine">One reader per source runs in parallel, then a single pass reasons over all of it.</p>
            </div>

            <p className="sr-only" role="status" aria-live="polite">
              {announce}
            </p>
          </form>
              </div>

              <section className="panel ob-cell ob-c-graph ob-graph" aria-labelledby="ob-graph-title">
                <div className="ob-card-head">
                  <h2 id="ob-graph-title" className="ob-card-title serif">
                    Your graph
                  </h2>
                  <p className="ob-card-sub">
                    {profile
                      ? `${profile.person.name}, ${profile.person.currentRole}`
                      : shown
                        ? 'A sample profile, so you can see the shape of it.'
                        : 'Your skills and next roles show here once it is built.'}
                  </p>
                </div>
                {axes.length >= 3 && (
                  <div className="ob-radar">
                    <Radar
                      axes={axes}
                      axisSetKey={profile ? 'home-mine' : 'home-sample'}
                      series={{ primary: profile ? 'You' : 'Sample' }}
                      ariaLabel={`${profile ? 'Your' : 'Sample'} skill radar: ${axes.length} domains`}
                      tableCaption="Domain scores, 0 to 100"
                    />
                  </div>
                )}
                {topRoles.length > 0 && (
                  <ol className="ob-roles" aria-label={profile ? 'Your top roles' : 'Top roles in the sample'}>
                    {topRoles.map((r) => (
                      <li key={r.id} className="ob-role">
                        <span className="ob-role-title">{r.title}</span>
                        <span className="kicker">{r.eta}</span>
                      </li>
                    ))}
                  </ol>
                )}
                {profile ? (
                  <button type="button" className="btn ob-graph-cta" onClick={() => actions.showGraph()}>
                    Open my graph
                    <IconArrowRight size={16} strokeWidth={1.75} />
                  </button>
                ) : hasSample ? (
                  <>
                    <button type="button" className="btn ob-graph-cta" onClick={() => void openSample()} disabled={sampleBusy}>
                      {sampleBusy ? 'Opening the sample…' : 'Open the sample graph'}
                      {!sampleBusy && <IconArrowRight size={16} strokeWidth={1.75} />}
                    </button>
                    {sampleError && (
                      <p className="ob-error" role="alert">
                        {sampleError}
                      </p>
                    )}
                  </>
                ) : null}
              </section>

              <nav className="ob-cell ob-c-tiles ob-tiles" aria-label="Tools">
                <button type="button" className="panel ob-tile" onClick={() => actions.openMatch()}>
                  <span className="ob-tile-title serif">Check a posting</span>
                  <span className="ob-tile-line">Paste a posting and see each requirement against your evidence.</span>
                </button>
                <button type="button" className="panel ob-tile" onClick={() => actions.openPractice({ screen: 'hub' })}>
                  <span className="ob-tile-title serif">Practice the interview</span>
                  <span className="ob-tile-line">Speak or type answers to questions drawn from your own work.</span>
                </button>
                <button type="button" className="panel ob-tile" onClick={() => actions.openDiscover()}>
                  <span className="ob-tile-title serif">Discover jobs</span>
                  <span className="ob-tile-line">Search employers' own boards for roles that fit you.</span>
                </button>
                <button
                  type="button"
                  className="panel ob-tile"
                  onClick={() => (profile ? actions.showGraph() : focusForm())}
                  aria-describedby={profile ? undefined : 'ob-prep-note'}
                >
                  <span className="ob-tile-title serif">Prepare for a role</span>
                  <span className="ob-tile-line">
                    {profile ? 'Open a role on your graph and ask how to prepare.' : 'Plan the weeks to a role from your gaps.'}
                  </span>
                  {!profile && (
                    <span className="kicker" id="ob-prep-note">
                      {PREP_NOTE}
                    </span>
                  )}
                </button>
              </nav>

              <section className="panel ob-cell ob-c-how ob-how" aria-labelledby="ob-how-title">
                <h2 className="kicker ob-how-title" id="ob-how-title">
                  How it works
                </h2>
                <ol className="ob-steps" id="how" role="list">
                  {STEPS.map((s) => (
                    <li key={s.n} className="ob-step">
                      <span className="kicker">{s.n}</span>
                      <h3 className="serif">{s.title}</h3>
                      <p>{s.body}</p>
                    </li>
                  ))}
                </ol>
                <a className="btn btn-quiet ob-how-more" href="/how-it-works">
                  Read more about how it works
                </a>
              </section>

              <section className="panel ob-cell ob-c-recent ob-recent" aria-labelledby="ob-recent-title">
                <h2 className="kicker ob-how-title" id="ob-recent-title">
                  Recent
                </h2>
                {recent.length > 0 ? (
                  <ul className="ob-recent-list">
                    {recent.map((m) => (
                      <li key={m.id}>
                        <button type="button" className="ob-recent-row" onClick={() => actions.openMatch({ screen: 'match', id: m.id })}>
                          <span className="ob-recent-text">
                            <span className="ob-role-title">{m.title}</span>
                            {m.company && <span className="ob-recent-co">{m.company}</span>}
                          </span>
                          <span className="chip">{BAND_LABEL[m.band]}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ob-empty">Your matches will show here.</p>
                )}
              </section>
            </div>
          </motion.div>
        </main>

        <SiteFooter />
      </div>
    </div>
  );
}
