/**
 * The resume built for one posting. Left, a single-column preview with the fact ids behind every line; right, what
 * needs the person's decision before anything can be downloaded, what changed and why, and the checks the files pass.
 *
 * The preview, the decisions and the lists are exported separately: the store-connected screen composes them, the dev
 * gallery drives them from fixtures, and the parity script renders `ResumePreview`, `ChangeList` and `LintList`.
 * Nothing a model wrote is markup: bullets, prompts and notes are text nodes. A `[x]` placeholder is shown marked,
 * never filled in: a missing figure is a question to the person, not something the page may guess.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { Bullet, Change, Decision, DecisionChoice, DecisionChoiceKey, LintReport, Posting, ResumeVariant } from '../../shared/match';
import { CHANGE_LABEL, DECISION_CHIP, DECISION_LABEL, allBullets, groupFactIds, shownBullets, splitPlaceholders } from './model';
import { Cites, FactIds, RouteLink, Tick } from './parts';
import type { MatchRoute } from './route';
import type { MatchFact } from './types';

/* ───────────────────────── preview ───────────────────────── */

type OpenFact = (id: string, el: HTMLElement) => void;

function BulletText({ bullet }: { bullet: Bullet }) {
  const parts = splitPlaceholders(bullet.text, bullet.placeholders);
  return (
    <>
      {parts.map((p, i) =>
        p.ph ? (
          <span key={i} className="mt-ph">
            {p.text}
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
      {bullet.status === 'needs_user' && <span className="sr-only"> (needs your decision)</span>}
    </>
  );
}

function BulletItem({ bullet, onFact }: { bullet: Bullet; onFact?: OpenFact }) {
  return (
    <li className={bullet.status === 'needs_user' ? 'mt-flag' : undefined} data-bullet={bullet.id} data-status={bullet.status}>
      <BulletText bullet={bullet} />
      <FactIds ids={bullet.factIds} onOpen={onFact} />
    </li>
  );
}

interface EntryProps {
  entryFactId: string;
  title: string;
  org?: string;
  dates?: string;
  bullets: Bullet[];
  onFact?: OpenFact;
}

function Entry({ entryFactId, title, org, dates, bullets, onFact }: EntryProps) {
  const shown = shownBullets(bullets);
  return (
    <div className="mt-doc-entry" data-entry={entryFactId}>
      <div className="mt-doc-row">
        <strong>
          {title}
          {org ? `, ${org}` : ''}
          <FactIds ids={[entryFactId]} onOpen={onFact} />
        </strong>
        {dates && <span className="numeral mt-muted">{dates}</span>}
      </div>
      {shown.length > 0 && (
        <ul>
          {shown.map((b) => (
            <BulletItem key={b.id} bullet={b} onFact={onFact} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** The resume as the files will read: one column, standard headings, ASCII dates, contact as plain text. */
export function ResumePreview({ variant, onFact }: { variant: ResumeVariant; onFact?: OpenFact }) {
  const { header, summary, skills, experience, projects, education } = variant;
  const contact = [header.headline, ...header.contact].filter(Boolean).join(' · ');
  return (
    <article className="mt-doc" aria-label="Resume preview">
      <p className="mt-doc-name">{header.name}</p>
      {contact && <p className="mt-doc-contact">{contact}</p>}

      {summary.status !== 'dropped' && (
        <>
          <h3 className="mt-doc-h">Summary</h3>
          <p className={summary.status === 'needs_user' ? 'mt-flag' : undefined} data-bullet={summary.id} data-status={summary.status}>
            <BulletText bullet={summary} />
            <FactIds ids={summary.factIds} onOpen={onFact} />
          </p>
        </>
      )}

      {skills.length > 0 && (
        <>
          <h3 className="mt-doc-h">Skills</h3>
          {skills.map((g) => (
            <p key={g.group} data-group={g.group}>
              <strong>{g.group}:</strong> {g.items.map((i) => i.name).join(', ')}
              <FactIds ids={groupFactIds(g)} onOpen={onFact} />
            </p>
          ))}
        </>
      )}

      {experience.length > 0 && (
        <>
          <h3 className="mt-doc-h">Experience</h3>
          {experience.map((e) => (
            <Entry key={e.entryFactId} {...e} onFact={onFact} />
          ))}
        </>
      )}

      {projects.length > 0 && (
        <>
          <h3 className="mt-doc-h">Projects</h3>
          {projects.map((e) => (
            <Entry key={e.entryFactId} {...e} onFact={onFact} />
          ))}
        </>
      )}

      {education.length > 0 && (
        <>
          <h3 className="mt-doc-h">Education</h3>
          {education.map((e) => (
            <div key={e.factId} className="mt-doc-row" data-entry={e.factId}>
              <span>
                {e.title}
                {e.org ? `, ${e.org}` : ''}
                <FactIds ids={[e.factId]} onOpen={onFact} />
              </span>
              {e.dates && <span className="numeral mt-muted">{e.dates}</span>}
            </div>
          ))}
        </>
      )}
    </article>
  );
}

/* ───────────────────────── what changed, and the checks ───────────────────────── */

export function ChangeList({ changes, facts }: { changes: readonly Change[]; facts: readonly MatchFact[] }) {
  if (changes.length === 0) return <p className="mt-muted">Nothing was moved or reworded: this is your resume as it was, in the posting's order.</p>;
  return (
    <ul className="mt-rows">
      {changes.map((c, i) => (
        <li key={i} className="mt-prop" data-change={c.kind}>
          <span className={`mt-chip mt-ch-${c.kind}`}>{CHANGE_LABEL[c.kind]}</span>
          <div>
            <p>{c.text}</p>
            <Cites factIds={c.factIds} facts={facts} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function LintList({ lint }: { lint?: LintReport }) {
  if (!lint || lint.items.length === 0) {
    return <p className="mt-muted">The checks run on the exported files and appear here.</p>;
  }
  return (
    <>
      <ul className="mt-checks">
        {lint.items.map((item) => (
          <li key={item.id} className="mt-check" data-lint={item.id} data-ok={item.ok}>
            <Tick ok={item.ok} />
            <span>
              {item.label}
              {item.detail && <span className="mt-check-detail"> · {item.detail}</span>}
              {item.location && <span className="mt-cite-src"> {item.location}</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-fine">These are the checks applicant systems actually need. No vendor "pass" scores: nobody knows their rules.</p>
    </>
  );
}

/* ───────────────────────── decisions ───────────────────────── */

const OPTION_ORDER: DecisionChoiceKey[] = ['keep', 'soften', 'drop', 'use_posting_term', 'keep_my_wording', 'fill'];

interface DecisionsProps {
  decisions: readonly Decision[];
  variant: ResumeVariant;
  facts: readonly MatchFact[];
  saving: ReadonlySet<string>;
  onDecide: (choice: DecisionChoice) => void;
  headingRef?: (el: HTMLHeadingElement | null) => void;
}

export function Decisions({ decisions, variant, facts, saving, onDecide, headingRef }: DecisionsProps) {
  const bullets = new Map(allBullets(variant).map((b) => [b.id, b]));
  const n = decisions.length;
  return (
    <section className="panel mt-panel" aria-labelledby="mt-dec-h">
      <div className="mt-panel-head">
        <h2 id="mt-dec-h" ref={headingRef} tabIndex={-1} className="kicker mt-h2">
          {n > 0 ? (
            <>
              Needs your decision · <span className="numeral">{n}</span>
            </>
          ) : (
            'Needs your decision'
          )}
        </h2>
        <span className="mt-muted">{n > 0 ? 'Nothing ships until these are settled' : 'Nothing is waiting on you'}</span>
      </div>
      {n === 0 ? (
        <p className="mt-callout is-ok" role="status">
          <Tick ok />
          Every line is settled. The files are ready to download.
        </p>
      ) : (
        <ul className="mt-rows">
          {decisions.map((d) => (
            <DecisionRow key={d.bulletId} decision={d} bullet={bullets.get(d.bulletId)} facts={facts} busy={saving.has(d.bulletId)} onDecide={onDecide} />
          ))}
        </ul>
      )}
    </section>
  );
}

function DecisionRow({
  decision: d,
  bullet,
  facts,
  busy,
  onDecide,
}: {
  decision: Decision;
  bullet?: Bullet;
  facts: readonly MatchFact[];
  busy: boolean;
  onDecide: (choice: DecisionChoice) => void;
}) {
  const inputId = useId();
  const [figure, setFigure] = useState('');
  const options = OPTION_ORDER.filter((o) => d.options.includes(o) && o !== 'fill');
  const canFill = d.kind === 'number' && d.options.includes('fill');
  const decide = (choice: DecisionChoiceKey, value?: string) => onDecide({ bulletId: d.bulletId, choice, ...(value !== undefined ? { value } : {}) });
  const primary: DecisionChoiceKey | undefined = d.kind === 'stretch' ? 'soften' : d.kind === 'confirm_term' ? 'use_posting_term' : undefined;

  return (
    <li className="mt-prop" data-decision={d.bulletId} data-kind={d.kind} aria-busy={busy}>
      <span className={`mt-chip ${d.kind === 'stretch' ? 'mt-st-missing_gap' : 'mt-st-unknown'}`}>{DECISION_CHIP[d.kind]}</span>
      <div className="mt-prop-body">
        {bullet && (
          <p className="mt-prop-line">
            <q>{bullet.text}</q>
          </p>
        )}
        <p className="mt-note">{d.prompt}</p>
        {bullet && <Cites factIds={bullet.factIds} facts={facts} />}

        {canFill && (
          <div className="mt-fill">
            <label htmlFor={inputId} className="sr-only">
              The figure, in your own words
            </label>
            <input
              id={inputId}
              className="field mt-fill-input"
              type="text"
              inputMode="decimal"
              autoComplete="off"
              placeholder="e.g. 38"
              value={figure}
              readOnly={busy}
              onChange={(e) => setFigure(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && figure.trim() && !busy) {
                  e.preventDefault();
                  decide('fill', figure.trim());
                }
              }}
            />
            <button type="button" className="btn btn-sm mt-btn-sm" disabled={!figure.trim() || busy} onClick={() => decide('fill', figure.trim())}>
              {DECISION_LABEL.fill}
            </button>
          </div>
        )}

        <div className="mt-choices">
          {options.map((o) => (
            <button
              key={o}
              type="button"
              className={`btn btn-sm mt-btn-sm${o === primary ? ' btn-primary' : ''}`}
              disabled={busy}
              onClick={() => decide(o)}
            >
              {o === 'drop' && d.kind === 'number' ? 'Drop the line' : DECISION_LABEL[o]}
            </button>
          ))}
        </div>
        {busy && (
          <p className="mt-muted" role="status">
            Saving…
          </p>
        )}
      </div>
    </li>
  );
}

/* ───────────────────────── the screen ───────────────────────── */

export interface ResumeViewProps {
  posting?: Posting;
  facts: readonly MatchFact[];
  variant: ResumeVariant;
  matchId: string;
  saving: ReadonlySet<string>;
  /** A transient notice (a failed save, a 409 on download). */
  notice?: string;
  /** The file being fetched right now. */
  downloading?: 'docx' | 'pdf';
  onDecide: (choice: DecisionChoice) => void;
  onNavigate: (route: MatchRoute) => void;
  onDownload: (kind: 'docx' | 'pdf') => void;
  onFact?: OpenFact;
  hrefFor: (kind: 'docx' | 'pdf') => string;
  /**
   * The person chose "Fit my resume to this role, then apply" on the match: where "Use this resume and apply" goes
   * (`/apply/new?match=<id>&resume=tailored`). It opens, like the downloads, once every decision is settled.
   */
  applyHref?: string;
}

export function ResumeView(props: ResumeViewProps) {
  const { posting, facts, variant, matchId, saving, notice, downloading, onDecide, onNavigate, onDownload, onFact, hrefFor, applyHref } = props;
  const pending = variant.decisions.length;
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const prev = useRef(pending);

  // a decision leaves the list when it is settled: keep the keyboard inside the panel instead of losing it to <body>
  useEffect(() => {
    if (pending < prev.current && (document.activeElement === document.body || document.activeElement === null)) headingRef.current?.focus({ preventScroll: true });
    prev.current = pending;
  }, [pending]);

  const title = [posting?.title, posting?.company].filter(Boolean).join(' · ');
  const jumpToDecisions = () => {
    const first = document.querySelector<HTMLElement>('[data-decision] button:not([disabled]), [data-decision] input');
    (first ?? headingRef.current)?.focus();
  };

  return (
    <>
      <section className="mt-intro mt-intro-split">
        <div className="mt-intro-text">
          <p className="kicker">
            <RouteLink route={{ screen: 'match', id: matchId }} onNavigate={onNavigate} className="mt-plain">
              ← Match
            </RouteLink>{' '}
            · Resume for this role
          </p>
          <h1 className="serif mt-title" data-mt-h1 tabIndex={-1}>
            {title || 'Resume for this role'}
          </h1>
          <p className="mt-lede">
            Chosen and ordered from your facts for this posting. Every line cites a source.
            {pending > 0 ? (
              <>
                {' '}
                <span className="numeral">{pending}</span> {pending === 1 ? 'line needs' : 'lines need'} a decision from you before download.
              </>
            ) : (
              ' Everything is settled, so the files are ready.'
            )}
          </p>
        </div>
        <div className="mt-actions" role="group" aria-label={applyHref ? 'Downloads and applying' : 'Downloads'}>
          <RouteLink route={{ screen: 'cover', id: matchId }} onNavigate={onNavigate} className="btn btn-quiet mt-btn">
            Cover note
          </RouteLink>
          {(['docx', 'pdf'] as const).map((kind) =>
            pending > 0 ? (
              <button key={kind} type="button" className={`btn mt-btn${kind === 'pdf' && !applyHref ? ' btn-primary' : ''}`} aria-disabled="true" aria-describedby="mt-dl-hint" onClick={jumpToDecisions}>
                Download {kind.toUpperCase()}
              </button>
            ) : (
              <a
                key={kind}
                href={hrefFor(kind)}
                download
                className={`btn mt-btn${kind === 'pdf' && !applyHref ? ' btn-primary' : ''}`}
                aria-busy={downloading === kind}
                onClick={(e) => {
                  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
                  e.preventDefault();
                  onDownload(kind);
                }}
              >
                {downloading === kind ? 'Preparing…' : `Download ${kind.toUpperCase()}`}
              </a>
            ),
          )}
          {applyHref &&
            (pending > 0 ? (
              <button type="button" className="btn btn-primary mt-btn" aria-disabled="true" aria-describedby="mt-dl-hint" onClick={jumpToDecisions}>
                Use this resume and apply
              </button>
            ) : (
              <a className="btn btn-primary mt-btn" href={applyHref}>
                Use this resume and apply
              </a>
            ))}
        </div>
      </section>
      {pending > 0 && (
        <>
          <p id="mt-dl-hint" className="mt-muted mt-hint">
            {applyHref ? 'Downloads and applying open' : 'Downloads open'} once the {pending === 1 ? 'decision' : 'decisions'} on the right {pending === 1 ? 'is' : 'are'} settled.
          </p>
          {/* the preview below carries a tab stop for every source: this takes the keyboard straight to the decisions */}
          <button type="button" className="mt-skip" onClick={jumpToDecisions}>
            Skip to what needs your decision
          </button>
        </>
      )}
      <p className={`mt-status${notice ? ' is-error' : ''}`} role={notice ? 'alert' : 'status'}>
        {notice}
      </p>

      <div className="mt-split">
        <section className="panel mt-panel mt-doc-panel" aria-label="Preview">
          <h2 className="sr-only">Resume preview</h2>
          <ResumePreview variant={variant} onFact={onFact} />
        </section>

        <div className="mt-stack">
          <Decisions
            decisions={variant.decisions}
            variant={variant}
            facts={facts}
            saving={saving}
            onDecide={onDecide}
            headingRef={(el) => {
              headingRef.current = el;
            }}
          />
          <section className="panel mt-panel" aria-labelledby="mt-chg-h">
            <h2 id="mt-chg-h" className="kicker mt-h2">
              What changed, and why
            </h2>
            <ChangeList changes={variant.changes} facts={facts} />
          </section>
          <section className="panel mt-panel" aria-labelledby="mt-lint-h">
            <h2 id="mt-lint-h" className="kicker mt-h2">
              Checked before download
            </h2>
            <LintList lint={variant.lint} />
          </section>
        </div>
      </div>
    </>
  );
}
