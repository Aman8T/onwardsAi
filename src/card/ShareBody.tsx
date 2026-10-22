/**
 * What is inside the share sheet (R1c, canvas https://claude.ai/artifact/LG8rk27K2RfG68giTXcoX5): the sharing switch, the
 * card as others will see it, where it goes (a GitHub README, LinkedIn, just the link), what it shows (up to three
 * skills, a role, a theme), and a new link or turning it off.
 *
 * It owns no overlay: `ShareSheet` puts it in a dialog, and the dev page puts it on a page. Everything the person changes
 * saves on its own (see `useCard`); the sheet only says "Saving" and shows the server's sentence when a save is refused.
 * Before sharing is on there is nothing public: the preview is drawn here in the page's colours. Once it is on, the
 * preview is the real image from `/r/{slug}.svg`, so the screen shows exactly what others see.
 *
 * The sample and an anonymous graph cannot share (the server says so with `blocked`): they get a sentence and one action,
 * never a switch. There are no ranks, percentiles or match percentages anywhere on it.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { CARD_SKILLS_MAX } from '../../shared/card';
import type { CardSkill, CardView } from '../../shared/card';
import type { CardClient } from './api';
import { CardPreview } from './CardPreview';
import { THEME_OPTIONS, cardLabel, imageSrc } from './format';
import { useCard } from './useCard';
import type { CardModel } from './useCard';
import '../styles/card.css';

export type ShareTab = 'github' | 'linkedin' | 'link';

const TABS: ReadonlyArray<{ id: ShareTab; label: string; short: string }> = [
  { id: 'github', label: 'GitHub README', short: 'GitHub' },
  { id: 'linkedin', label: 'LinkedIn', short: 'LinkedIn' },
  { id: 'link', label: 'Just the link', short: 'Link' },
];

const COPIED_MS = 2400;
const COPY_FAILED = "Your browser wouldn't copy it. Select the text and copy it yourself.";

export interface ShareBodyProps {
  profileId: string;
  client: CardClient;
  /** The phone layout: the small card, pill tabs, "What it shows" folded. */
  narrow: boolean;
  /** The id the dialog (or page) is named by: the heading carries it. */
  titleId: string;
  onClose?: () => void;
  /** The sample's action: leave it for the person's own resume. */
  onAnalyse: () => void;
  /** An anonymous graph's action; absent when the server offers no way to sign in. */
  onSignIn?: () => void;
  /** False on the dev page, which has no server to draw the real image: the card is drawn here as when the image cannot load. */
  liveImage?: boolean;
  /** The dev page only: start on a tab, with "Copied" showing, or with the new-link question open. */
  initial?: { tab?: ShareTab; copied?: boolean; confirmLink?: boolean };
}

export function ShareBody({ profileId, client, narrow, titleId, onClose, onAnalyse, onSignIn, liveImage = true, initial }: ShareBodyProps) {
  const card = useCard(profileId, client);
  return (
    <div className="cs-body" data-narrow={narrow ? 'true' : 'false'}>
      <header className="cs-head">
        <div className="cs-head-text">
          <p className="kicker">Share proof</p>
          <h2 id={titleId} className="serif cs-title">
            {narrow ? 'Share your skill shape' : 'Your skill shape, as a card'}
          </h2>
        </div>
        {onClose && (
          <button type="button" className="btn btn-quiet cs-close" aria-label="Close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" aria-hidden="true">
              <path d="M2 2l10 10M12 2L2 12" />
            </svg>
          </button>
        )}
      </header>

      {card.phase === 'loading' && <Loading />}
      {card.phase === 'failed' && (
        <div className="cs-stack">
          <p className="cs-error" role="alert">
            {card.loadProblem}
          </p>
          <div>
            <button type="button" className="btn" onClick={card.reload}>
              Try again
            </button>
          </div>
        </div>
      )}
      {card.phase === 'ready' && card.view && card.draft && (
        card.view.blocked ? (
          <Blocked kind={card.view.blocked} onAnalyse={onAnalyse} onSignIn={onSignIn} />
        ) : (
          <Panel card={card} view={card.view} narrow={narrow} live={liveImage} initial={initial} />
        )
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function Loading() {
  return (
    <div className="cs-stack" role="status">
      <p className="cs-note">Opening your card…</p>
      <div className="cs-skeleton" aria-hidden="true" />
    </div>
  );
}

function Blocked({ kind, onAnalyse, onSignIn }: { kind: NonNullable<CardView['blocked']>; onAnalyse: () => void; onSignIn?: () => void }) {
  if (kind === 'sample') {
    return (
      <div className="cs-blocked">
        <p>Sharing needs your own graph. This one is the sample everyone sees.</p>
        <button type="button" className="btn btn-primary cs-action" onClick={onAnalyse}>
          Analyse my resume
        </button>
      </div>
    );
  }
  return (
    <div className="cs-blocked">
      <p>Sharing needs an account, so you can turn the card off later. Sign in and this graph is saved to it.</p>
      {onSignIn && (
        <button type="button" className="btn btn-primary cs-action" onClick={onSignIn}>
          Sign in
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface PanelProps {
  card: CardModel;
  view: CardView;
  narrow: boolean;
  live: boolean;
  initial: ShareBodyProps['initial'];
}

function Panel({ card, view, narrow, live, initial }: PanelProps) {
  const { draft, saving, problem, linking } = card;
  const switchId = useId();
  const subId = useId();
  const [tab, setTab] = useState<ShareTab>(initial?.tab ?? 'github');
  const [confirming, setConfirming] = useState(initial?.confirmLink ?? false);
  const [settingsOpen, setSettingsOpen] = useState(!narrow);
  if (!draft) return null;

  const on = draft.shared;
  const linked = view.settings.shared && !!view.slug && !!view.pageUrl;
  const making = on && !linked;

  const switchLine = making ? 'Making your link…' : on ? 'Sharing is on' : 'Sharing is off';
  const switchSub = on ? 'Anyone with the link can see the card.' : 'Only you can see your graph.';

  const skills = draft.skills
    .map((id) => view.skillsOffered.find((s) => s.id === id))
    .filter((s): s is CardSkill => s !== undefined);
  const role = draft.roleId ? view.rolesOffered.find((r) => r.id === draft.roleId) : undefined;

  return (
    <div className="cs-stack">
      <div className="cs-switchrow">
        <button
          type="button"
          role="switch"
          className="cs-switch"
          aria-checked={on}
          aria-labelledby={switchId}
          aria-describedby={subId}
          disabled={linking}
          onClick={() => {
            setConfirming(false);
            card.change({ shared: !on }, true);
          }}
        >
          <span className="cs-track" aria-hidden="true">
            <span className="cs-knob" />
          </span>
        </button>
        <span className="cs-switchtext">
          <span id={switchId} className="cs-switchline">
            {switchLine}
          </span>
          <span id={subId} className="cs-sub">
            {switchSub}
          </span>
        </span>
        <span className="cs-saving" role="status">
          {saving ? 'Saving…' : ''}
        </span>
      </div>

      {problem && (
        <p className="cs-error" role="alert">
          {problem}
        </p>
      )}

      <Preview view={view} skills={skills} roleTitle={role?.title} narrow={narrow} shared={on} live={live} />

      {!on && (
        <p className="cs-lede">
          Turn it on and Leafout makes a random link for your card. Anyone with the link sees your areas and their scores. Your resume lines, your name and your
          email are never on it.
        </p>
      )}

      {on && (
        <>
          {linked ? <Where view={view} tab={tab} onTab={setTab} narrow={narrow} copiedAtStart={initial?.copied ?? false} /> : <p className="cs-note">Your link will appear here in a moment.</p>}

          <details
            className="cs-details"
            open={settingsOpen}
            onToggle={(e) => setSettingsOpen((e.currentTarget as HTMLDetailsElement).open)}
          >
            <summary>What it shows</summary>
            <div className="cs-settings">
              <fieldset className="cs-fieldset">
                <legend className="kicker">Skills to name (up to three)</legend>
                {view.skillsOffered.length === 0 && <p className="cs-note">No skills to name yet.</p>}
                {view.skillsOffered.map((s) => {
                  const picked = draft.skills.includes(s.id);
                  const full = !picked && draft.skills.length >= CARD_SKILLS_MAX;
                  return (
                    <label key={s.id} className="cs-check" data-disabled={full ? 'true' : 'false'}>
                      <input type="checkbox" checked={picked} disabled={full} onChange={() => card.toggleSkill(s.id)} />
                      <span>
                        {s.name} <span className="numeral cs-faint">{Math.round(s.score)}</span>
                      </span>
                    </label>
                  );
                })}
              </fieldset>

              <div className="cs-fieldrow">
                <label className="kicker" htmlFor={`${switchId}-role`}>
                  Next to a role
                </label>
                <select
                  id={`${switchId}-role`}
                  className="cs-select"
                  value={draft.roleId ?? ''}
                  onChange={(e) => card.change({ roleId: e.target.value || undefined })}
                >
                  <option value="">Just me</option>
                  {view.rolesOffered.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.title}
                    </option>
                  ))}
                </select>
              </div>

              <div className="cs-fieldrow">
                <label className="kicker" htmlFor={`${switchId}-theme`}>
                  Theme
                </label>
                <select
                  id={`${switchId}-theme`}
                  className="cs-select"
                  value={draft.theme}
                  onChange={(e) => card.change({ theme: e.target.value as typeof draft.theme })}
                >
                  {THEME_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </details>

          {linked && (
            <div className="cs-end">
              {confirming ? (
                <div className="cs-confirm" role="group" aria-label="Make a new link">
                  <p>The old link stops working at once, and a README that uses it shows “This card isn’t shared any more”.</p>
                  <div className="cs-row">
                    <button
                      type="button"
                      className="btn btn-primary cs-action"
                      disabled={linking}
                      onClick={() => {
                        void card.makeNewLink().then(() => setConfirming(false));
                      }}
                    >
                      {linking ? 'Making it…' : 'Make a new link'}
                    </button>
                    <button type="button" className="btn btn-quiet cs-action" disabled={linking} onClick={() => setConfirming(false)}>
                      Keep this one
                    </button>
                  </div>
                </div>
              ) : (
                <div className="cs-row">
                  <button type="button" className="btn cs-action" onClick={() => setConfirming(true)}>
                    Make a new link
                  </button>
                  <button type="button" className="btn btn-quiet cs-action" onClick={() => card.change({ shared: false }, true)}>
                    Turn off sharing
                  </button>
                </div>
              )}
              <p className="cs-fine">
                A new link stops the old one at once. The card follows your graph: analyse again and it changes within a day. A README shows the change within
                the hour.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

interface PreviewProps {
  view: CardView;
  skills: CardSkill[];
  roleTitle?: string;
  narrow: boolean;
  shared: boolean;
  live: boolean;
}

/** The real image once the server holds a shared card; the token-drawn card before that, and if the image cannot load. */
function Preview({ view, skills, roleTitle, narrow, shared, live: allowed }: PreviewProps) {
  const src = allowed ? imageSrc(view, narrow) : undefined;
  const [failed, setFailed] = useState<string | null>(null);
  const live = !!src && failed !== src;
  const linked = view.settings.shared && !!view.slug;

  if (live && src) {
    return (
      <figure className="cs-figure">
        <img
          className="cs-image"
          data-size={narrow ? 'sm' : 'md'}
          src={src}
          width={narrow ? 340 : 480}
          height={narrow ? 190 : 330}
          alt={cardLabel(view.areas)}
          onError={() => setFailed(src)}
        />
        {view.settings.theme === 'match' && <figcaption className="cs-fine">Shown light. Visitors on a dark page see the dark card.</figcaption>}
      </figure>
    );
  }

  const caption = src
    ? "The live image didn't load, so this is drawn here in your page's colours."
    : !shared
      ? 'A preview, drawn in your page’s colours. Nothing is public until you turn sharing on.'
      : !linked
        ? "Drawn here in your page's colours until your link is ready."
        : '';

  return (
    <figure className="cs-figure">
      <CardPreview areas={view.areas} skills={skills} roleTitle={roleTitle} size={narrow ? 'sm' : 'md'} updatedAt={view.updatedAt} />
      {caption && <figcaption className="cs-fine">{caption}</figcaption>}
    </figure>
  );
}

/* ------------------------------------------------------------------ */

interface WhereProps {
  view: CardView;
  tab: ShareTab;
  onTab: (t: ShareTab) => void;
  narrow: boolean;
  copiedAtStart: boolean;
}

/** A link only if it is an address of ours or a web address: never `javascript:` or the like. */
const safeHref = (u: string | undefined): string | undefined => (u && /^(https?:\/\/|\/)/i.test(u) ? u : undefined);

function Where({ view, tab, onTab, narrow, copiedAtStart }: WhereProps) {
  const list = useRef<HTMLDivElement>(null);
  const base = useId();
  const [copied, setCopied] = useState(copiedAtStart);
  const [copyProblem, setCopyProblem] = useState('');
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const pageUrl = view.pageUrl ?? '';
  const markdown = view.markdown ?? '';

  const copy = async (text: string) => {
    window.clearTimeout(timer.current);
    try {
      if (!navigator.clipboard) throw new Error('no clipboard');
      await navigator.clipboard.writeText(text);
      setCopyProblem('');
      setCopied(true);
      timer.current = window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      setCopied(false);
      setCopyProblem(COPY_FAILED);
    }
  };

  const pick = (next: ShareTab) => {
    setCopied(false);
    setCopyProblem('');
    onTab(next);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === tab);
    let to = i;
    if (e.key === 'ArrowRight') to = (i + 1) % TABS.length;
    else if (e.key === 'ArrowLeft') to = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === 'Home') to = 0;
    else if (e.key === 'End') to = TABS.length - 1;
    else return;
    e.preventDefault();
    pick(TABS[to].id);
    list.current?.querySelectorAll<HTMLElement>('[role="tab"]')[to]?.focus();
  };

  const copyLabel = copied ? 'Copied' : tab === 'github' ? 'Copy for GitHub' : 'Copy the link';
  const ogHref = safeHref(view.ogUrl);
  const pageHref = safeHref(view.pageUrl);

  return (
    <div className="cs-where">
      <div ref={list} className="cs-tabs" role="tablist" aria-label="Where it goes" onKeyDown={onKeyDown}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`${base}-${t.id}-tab`}
            className="cs-tab"
            aria-selected={tab === t.id}
            aria-controls={`${base}-panel`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => pick(t.id)}
          >
            {narrow ? t.short : t.label}
          </button>
        ))}
      </div>

      <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-${tab}-tab`} className="cs-panel">
        {tab === 'github' && (
          <>
            <pre className="cs-code" tabIndex={0}>
              {markdown}
            </pre>
            {view.settings.theme === 'match' && <p className="cs-fine">Light visitors see the light card, dark visitors the dark one.</p>}
            <div className="cs-row">
              <button type="button" className="btn btn-primary cs-action" onClick={() => void copy(markdown)}>
                {copyLabel}
              </button>
              <span className="cs-fine">Paste it into your profile README.</span>
            </div>
          </>
        )}
        {tab === 'linkedin' && (
          <>
            <p className="cs-copy">Post the link: LinkedIn shows the card as a large image. It is the same card, drawn wide.</p>
            <div className="cs-row">
              <button type="button" className="btn btn-primary cs-action" onClick={() => void copy(pageUrl)}>
                {copyLabel}
              </button>
              {ogHref && (
                <a className="btn cs-action" href={ogHref} target="_blank" rel="noopener noreferrer">
                  See the image
                </a>
              )}
            </div>
          </>
        )}
        {tab === 'link' && (
          <>
            <label className="kicker" htmlFor={`${base}-link`}>
              Your link
            </label>
            <input id={`${base}-link`} className="cs-input" type="text" readOnly value={pageUrl} onFocus={(e) => e.currentTarget.select()} />
            <div className="cs-row">
              <button type="button" className="btn btn-primary cs-action" onClick={() => void copy(pageUrl)}>
                {copyLabel}
              </button>
              {pageHref && (
                <a className="btn cs-action" href={pageHref} target="_blank" rel="noopener noreferrer">
                  Open the page
                </a>
              )}
            </div>
          </>
        )}
        <span className="sr-only" role="status">
          {copied ? 'Copied to your clipboard.' : ''}
        </span>
        {copyProblem && (
          <p className="cs-error" role="alert">
            {copyProblem}
          </p>
        )}
      </div>
    </div>
  );
}
