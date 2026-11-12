/**
 * The empty dashboard (board "Dashboard · /dashboard · empty, upload popup"): the header, the quiet regions the
 * dashboard will fill once a graph exists, and one popup that asks for exactly two things, a resume and public
 * links, plus a sign-in row when nobody is signed in. No sample graph, no tiles, no how-it-works: nothing competes
 * with the one action. The ambient field is mounted once in index.html and the cursor light lives in App.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ClipboardEvent, CSSProperties, FormEvent, KeyboardEvent } from 'react';
import { AnimatePresence, motion, useAnimate, useReducedMotion } from 'motion/react';
import type { SourceKind } from '../../shared/types';
import { AccountMenu } from '../auth/AccountMenu';
import { offersSignIn } from '../auth/links';
import { signInSheet, useMe } from '../auth/useMe';
import { Brand } from '../components/Brand';
import { SchemeToggle } from '../components/SchemeToggle';
import { IconArrowRight, IconDoc, IconUpload } from '../components/Icons';
import { EASE, NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { useHandoffMessage } from '../lib/useHandoff';
import { isDiscoverPath } from '../discover/route';
import { isPracticePath } from '../practice/route';
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

/** The regions the dashboard fills once a graph exists, drawn behind the popup so the person sees what comes next. */
const GHOSTS: readonly { span: 'wide' | 'half' | 'third'; name: string; line?: string }[] = [
  { span: 'wide', name: 'Your graph', line: 'Where you stand today, and the roles you could grow into.' },
  { span: 'half', name: 'Next roles', line: 'Ranked by how much of each role you already cover.' },
  { span: 'third', name: 'Posting matches' },
  { span: 'third', name: 'Discover' },
  { span: 'third', name: 'Practice' },
];

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
                  <span className="ob-drop-fine">
                    Drop your resume here or <span className="ob-browse">browse</span>
                  </span>
                  <span className="ob-drop-touch">
                    <span className="ob-browse">Upload</span> your resume
                  </span>
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
  const [announce, setAnnounce] = useState('');

  const [hintScope, animateHint] = useAnimate();

  const profile = useApp((st) => st.profile);
  const handoffMessage = useHandoffMessage();
  const { user, loaded, providers } = useMe();
  // the sign-in row shows only once the server has said nobody is signed in, and only when it offers a way in
  const signedOut = loaded && !user && offersSignIn(providers);

  // Someone who opened /practice or /discover without a graph lands here: say why, and title the tab for it.
  const [wanted] = useState<'practice' | 'discover' | null>(() => {
    try {
      const path = window.location.pathname;
      return isPracticePath(path) ? 'practice' : isDiscoverPath(path) ? 'discover' : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!wanted || profile) return;
    const before = document.title;
    document.title = wanted === 'practice' ? 'Practice the interview · Leafout' : 'Discover roles · Leafout';
    return () => {
      document.title = before;
    };
  }, [wanted, profile]);

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
  const ready = !!file || links.length > 0 || pendingLink;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!ready) {
      // nothing to read yet: put focus on the first empty field (the drop zone) so its hint is announced,
      // and pulse the hint once (opacity only, --d-base; skipped under reduced motion)
      (e.currentTarget as HTMLFormElement).querySelector<HTMLElement>('.ob-drop-main, input')?.focus();
      if (!reduce && hintScope.current) void animateHint(hintScope.current, { opacity: [1, 0.35, 1] }, { duration: 0.25, ease: EASE });
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
    void actions.startAnalysis({ file, links: finalLinks.map((l) => l.href) });
  };

  const scrollToTop = () => window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });

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
      </div>

        <main className="m-screen ob-stage">
          {/* the hero: one line, and what it takes */}
          <section className="m-hero ob-hero" aria-label="Start here">
            <span className="kicker">Start here</span>
            <h1 id="ob-card-title" className="ob-card-title serif ob-h1">
              Add your resume.
            </h1>
            {wanted && !profile && (
              <p className="ob-card-sub ob-need" role="status">
                {wanted === 'practice' ? 'Practice' : 'Discover'} uses your skill graph. Build it first, about two minutes.
              </p>
            )}
            <p className="ob-card-sub ob-lede">That is all it takes. Your public links sharpen it. Every score cites a line from what you give us, and nothing else.</p>
            <div className="stick is-flow m-turn-l ob-nudge" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties}>
              <div className="postit">
                <span className="pen">No resume to hand? A GitHub or portfolio link is enough to begin.</span>
              </div>
            </div>
          </section>

          <div className="m-mosaic ob-mosaic">
          <motion.form
            className="m-tile m-plain m-c6 ob-form ob-popup"
            role="dialog"
            aria-labelledby="ob-card-title"
            onSubmit={onSubmit}
            noValidate
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
          >
            {handoffMessage && (
              <p className="ob-error ob-handoff" role="alert">
                {handoffMessage}
              </p>
            )}

            <ResumeDrop file={file} error={fileError} onPick={pickFile} onRemove={removeFile} />

            {/* links */}
            <div className="ob-field ob-field-links">
              <span className="kicker ob-label" id="ob-links-label">
                Links <span className="ob-optional">· optional</span>
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
                        {l.kind === 'linkedin' && <p className="ob-note">LinkedIn usually blocks automated reading. A PDF of your profile, dropped as the resume, is read in full.</p>}
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
                    placeholder="GitHub, Lovable, LinkedIn or a portfolio"
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

            {/* submit */}
            <div className="ob-submit">
              <button type="submit" className="btn btn-primary m-ink ob-cta" aria-disabled={!ready} aria-describedby={ready ? undefined : 'ob-hint'}>
                Build my skill graph
                <IconArrowRight size={16} strokeWidth={1.75} />
              </button>
              <AnimatePresence initial={false} mode="wait">
                {ready ? (
                  <motion.p key="fine" className="kicker ob-fine" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2, ease: EASE }}>
                    About a minute. Your files are read once and never shared.
                  </motion.p>
                ) : (
                  <motion.p key="hint" id="ob-hint" className="ob-hint" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2, ease: EASE }}>
                    <span ref={hintScope} className="ob-hint-text">
                      Add a resume or a link to begin.
                    </span>
                  </motion.p>
                )}
              </AnimatePresence>
            </div>

            {signedOut && (
              <div className="ob-signin">
                <span className="ob-signin-text">Have an account? Sign in to keep your graph on every device.</span>
                <button type="button" className="btn btn-sm" onClick={() => signInSheet.open('account')}>
                  Sign in
                </button>
              </div>
            )}

            <p className="sr-only" role="status" aria-live="polite">
              {announce}
            </p>
          </motion.form>

          {/* where the dashboard's pieces will go once a graph exists: quiet and inert */}
          {GHOSTS.map((g) => (
            <div key={g.name} className={`m-tile m-quiet ob-ghost-cell is-${g.span}`} aria-hidden="true">
              <span className="kicker">{g.name}</span>
              {g.line && <span className="m-fine">{g.line}</span>}
            </div>
          ))}
          </div>
        </main>
    </div>
  );
}
