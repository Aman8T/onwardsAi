/**
 * The cover note, paragraph by paragraph, with what each paragraph rests on beside it: the posting line it answers
 * and the facts behind it. A paragraph that rests on something the person said carries a "From you" chip. If a
 * paragraph has no fact it isn't written; the only fact-free lines are a closing sentence that claims nothing.
 */
import { useState } from 'react';
import type { Coverage, CoverNote, CoverParagraph, Posting, Requirement } from '../../shared/match';
import { factIndex, isUserFact } from './model';
import { Cites, FromYouChip, RouteLink, StatusChip, Tick, TierChip } from './parts';
import type { MatchRoute } from './route';
import type { MatchFact } from './types';

export interface CoverViewProps {
  posting?: Posting;
  facts: readonly MatchFact[];
  coverage: readonly Coverage[];
  note: CoverNote;
  matchId: string;
  onNavigate: (route: MatchRoute) => void;
  hrefPdf: string;
  onDownload: () => void;
  downloading?: boolean;
  notice?: string;
}

/** Requirements the note does not mention, in the posting's order: "left out on purpose". */
export function leftOut(posting: Posting | undefined, note: CoverNote): Requirement[] {
  if (!posting) return [];
  const used = new Set(note.paragraphs.flatMap((p) => p.requirementIds));
  return posting.requirements.filter((r) => !used.has(r.id));
}

const KIND_NOTE: Partial<Record<CoverParagraph['kind'], string>> = {
  gap: 'A gap, said once and plainly. It is not hidden and not dressed up.',
  close: 'A closing line. It makes no claim about you.',
};

export function CoverView({ posting, facts, coverage, note, matchId, onNavigate, hrefPdf, onDownload, downloading, notice }: CoverViewProps) {
  const [copied, setCopied] = useState('');
  const byId = factIndex(facts);
  const reqs = new Map((posting?.requirements ?? []).map((r) => [r.id, r]));
  const cov = new Map(coverage.map((c) => [c.requirementId, c]));
  const passed = note.lint?.items.filter((i) => i.ok) ?? [];
  const failed = note.lint?.items.filter((i) => !i.ok) ?? [];
  const noOpeners = note.lint?.items.some((i) => i.id === 'openers' && i.ok) ?? false;
  const skipped = leftOut(posting, note);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(note.paragraphs.map((p) => p.text).join('\n\n'));
      setCopied('Copied.');
    } catch {
      setCopied('Copying is blocked here. Select the text and copy it.');
    }
  };

  return (
    <>
      <section className="mt-intro mt-intro-split">
        <div className="mt-intro-text">
          <p className="kicker">
            <RouteLink route={{ screen: 'resume', id: matchId }} onNavigate={onNavigate} className="mt-plain">
              ← Resume
            </RouteLink>{' '}
            · Cover note
          </p>
          <h1 className="serif mt-title" data-mt-h1 tabIndex={-1}>
            Cover note{posting?.company ? ` · ${posting.company}` : ''}
          </h1>
          <p className="mt-lede">
            Each paragraph pairs one thing the posting asks for with one thing you can show. A gap is said plainly.{' '}
            <span className="numeral">{note.wordCount}</span> words{noOpeners ? '. No generic openers.' : '.'}
          </p>
        </div>
        <div className="mt-actions" role="group" aria-label="Cover note">
          <button type="button" className="btn mt-btn" onClick={copy}>
            Copy text
          </button>
          <a
            href={hrefPdf}
            download
            className="btn btn-primary mt-btn"
            aria-busy={downloading}
            onClick={(e) => {
              if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
              e.preventDefault();
              onDownload();
            }}
          >
            {downloading ? 'Preparing…' : 'Download PDF'}
          </a>
        </div>
      </section>
      <p className={`mt-status${notice ? ' is-error' : ''}`} role={notice ? 'alert' : 'status'}>
        {notice || copied}
      </p>

      <section className="panel mt-panel mt-cover" aria-label="The note and where each line comes from">
        <div className="mt-cover-head" aria-hidden="true">
          <span className="kicker">The note</span>
          <span className="kicker">Where each line comes from</span>
        </div>
        {note.paragraphs.map((p, i) => {
          const fromYou = p.factIds.some((id) => isUserFact(id, byId));
          return (
            <article key={i} className="mt-para" data-kind={p.kind} data-para={i}>
              <p className="mt-para-text">{p.text}</p>
              <div className="mt-why">
                {(p.requirementIds.length > 0 || fromYou || p.kind === 'logistics') && (
                  <div className="mt-chips">
                    {p.requirementIds.map((id) => {
                      const r = reqs.get(id);
                      const c = cov.get(id);
                      return r ? (
                        <span key={id} className="mt-chips-pair">
                          <TierChip tier={r.tier} />
                          {c && <StatusChip status={c.status} />}
                        </span>
                      ) : null;
                    })}
                    {p.kind === 'logistics' && <span className="mt-chip mt-tier">Logistics</span>}
                    {fromYou && <FromYouChip />}
                  </div>
                )}
                {p.requirementIds.map((id) => {
                  const r = reqs.get(id);
                  return r ? (
                    <p key={id} className="mt-why-line" data-req={id}>
                      Posting:{' '}
                      <q>{r.quote}</q>
                    </p>
                  ) : null;
                })}
                <Cites factIds={p.factIds} facts={facts} />
                {KIND_NOTE[p.kind] && p.factIds.length === 0 && <p className="mt-why-line">{KIND_NOTE[p.kind]}</p>}
              </div>
            </article>
          );
        })}
      </section>

      <section className="mt-trio" aria-label="How the note was checked">
        <div className="panel mt-panel">
          <h2 className="kicker mt-h2">Left out on purpose</h2>
          {skipped.length === 0 ? (
            <p className="mt-muted">Every requirement the posting lists is mentioned.</p>
          ) : (
            <>
              <p className="mt-muted">The note keeps to what your facts can back. It does not mention:</p>
              <ul className="mt-leftout">
                {skipped.slice(0, 5).map((r) => (
                  <li key={r.id}>{r.text}</li>
                ))}
              </ul>
              {skipped.length > 5 && <p className="mt-muted">and {skipped.length - 5} more.</p>}
            </>
          )}
        </div>
        <div className="panel mt-panel">
          <h2 className="kicker mt-h2">Checked</h2>
          {passed.length === 0 && failed.length === 0 ? (
            <p className="mt-muted">The checks run on the finished note and appear here.</p>
          ) : (
            <ul className="mt-checks">
              {[...passed, ...failed].map((i) => (
                <li key={i.id} className="mt-check" data-lint={i.id} data-ok={i.ok}>
                  <Tick ok={i.ok} />
                  <span>
                    {i.label}
                    {i.detail && <span className="mt-check-detail"> · {i.detail}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="panel mt-panel">
          <h2 className="kicker mt-h2">If a paragraph has no fact</h2>
          <p className="mt-muted">It isn't written. A posting requirement with nothing behind it gets a plain gap sentence or nothing at all.</p>
        </div>
      </section>
    </>
  );
}
