/**
 * Small parts the match screens share: status chips, citations and the superscript fact ids.
 *
 * Every chip carries its label as text; colour is only ever the second cue (fill, outline or dash tell the
 * statuses apart by lightness and shape in match.css). Everything a model wrote (`note`, a quote, a prompt) is
 * rendered as a text node: React escapes it, and there is no markup path from data to the page.
 */
import type { MouseEvent, ReactNode } from 'react';
import type { CoverageStatus, GateVerdict, RequirementTier } from '../../shared/match';
import type { MatchFact } from './types';
import { GATE_LABEL, STATUS_LABEL, TIER_LABEL, citeOf, describeRef, factIndex } from './model';
import { matchPath } from './route';
import type { MatchRoute } from './route';
import '../styles/match.css';

export function StatusChip({ status, count }: { status: CoverageStatus; count?: number }) {
  return (
    <span className={`mt-chip mt-st-${status}`} data-status={status}>
      {STATUS_LABEL[status]}
      {count !== undefined && (
        <>
          {' · '}
          <span className="numeral">{count}</span>
        </>
      )}
    </span>
  );
}

export function GateChip({ verdict }: { verdict: GateVerdict }) {
  return (
    <span className={`mt-chip mt-gv-${verdict}`} data-verdict={verdict}>
      {GATE_LABEL[verdict]}
    </span>
  );
}

export function TierChip({ tier }: { tier: RequirementTier }) {
  return (
    <span className="mt-chip mt-tier" data-tier={tier}>
      {TIER_LABEL[tier]}
    </span>
  );
}

/** "From you": the fact is something the person said, not something a document says. */
export function FromYouChip() {
  return <span className="mt-chip mt-from-you">From you</span>;
}

/**
 * `resume · line 16` then the words of the source, in mono. One line per distinct source: facts that cite the very
 * same passage (a skill drawn from a bullet, say) share a line, and every fact id still sits on it in `data-fact`.
 */
export function Cites({ factIds, facts, limit }: { factIds: readonly string[]; facts: readonly MatchFact[]; limit?: number }) {
  if (factIds.length === 0) return null;
  const byId = factIndex(facts);
  const groups = new Map<string, { ids: string[]; cite: ReturnType<typeof citeOf> }>();
  for (const id of factIds) {
    const cite = citeOf(id, byId);
    const key = cite.quote ? `${cite.source}|${cite.where}|${cite.quote}` : `id:${id}`;
    const g = groups.get(key);
    if (g) g.ids.push(id);
    else groups.set(key, { ids: [id], cite });
  }
  const all = [...groups.values()];
  const shown = limit === undefined ? all : all.slice(0, limit);
  const more = all.length - shown.length;
  return (
    <ul className="mt-cites" aria-label="Sources">
      {shown.map(({ ids, cite: c }) => (
        <li key={ids[0]} className="mt-cite" data-fact={ids.join(' ')}>
          <span className="mt-cite-src">{[c.source || ids[0], c.where].filter(Boolean).join(' · ')}</span>
          {c.quote && (
            <>
              {' '}
              <q className="mt-cite-quote">{c.quote}</q>
            </>
          )}
        </li>
      ))}
      {more > 0 && <li className="mt-cite mt-cite-more">and {more} more</li>}
    </ul>
  );
}

/** Every source of one fact, for the fact sheet. */
export function FactRefs({ fact }: { fact: MatchFact }) {
  return (
    <ul className="mt-cites">
      {fact.refs.map((ref, i) => {
        const c = describeRef(ref);
        return (
          <li key={i} className="mt-cite" data-ref={i}>
            <span className="mt-cite-src">{[c.source, c.where].filter(Boolean).join(' · ')}</span>{' '}
            <q className="mt-cite-quote">{c.quote}</q>
          </li>
        );
      })}
    </ul>
  );
}

/**
 * The superscript fact ids after a resume line. With `onOpen` each id is a button that opens the fact sheet;
 * without it (a static render) they are plain text.
 */
export function FactIds({ ids, onOpen }: { ids: readonly string[]; onOpen?: (id: string, el: HTMLElement) => void }) {
  if (ids.length === 0) return null;
  return (
    <sup className="mt-sups">
      {ids.map((id) =>
        onOpen ? (
          <button key={id} type="button" className="mt-sup" data-fact={id} aria-label={`Source ${id}`} onClick={(e) => onOpen(id, e.currentTarget)}>
            {id}
          </button>
        ) : (
          <span key={id} className="mt-sup" data-fact={id}>
            {id}
          </span>
        ),
      )}
    </sup>
  );
}

/** A check mark or a dash, drawn, with the state also written out for screen readers. */
export function Tick({ ok }: { ok: boolean }) {
  return (
    <>
      <svg className={`mt-tick${ok ? '' : ' is-off'}`} width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false">
        {ok ? <path d="M3.5 8.5l3 3 6-7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /> : <path d="M4 8h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />}
      </svg>
      <span className="sr-only">{ok ? 'Passed: ' : 'Needs attention: '}</span>
    </>
  );
}

/** A link to a match screen: a real anchor (new tab, copy link) that a plain click turns into in-app navigation. */
export function RouteLink({
  route,
  onNavigate,
  className,
  current,
  children,
}: {
  route: MatchRoute;
  onNavigate: (route: MatchRoute) => void;
  className?: string;
  /** This link names the page the person is on. */
  current?: boolean;
  children: ReactNode;
}) {
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onNavigate(route);
  };
  return (
    <a href={matchPath(route)} className={className} aria-current={current ? 'page' : undefined} onClick={click}>
      {children}
    </a>
  );
}
