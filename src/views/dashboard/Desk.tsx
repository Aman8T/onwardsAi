/**
 * Two pieces of the one-screen Home that read what the person has been doing here:
 *
 *   PickTile      "Pick up where you left off": the last thing touched, large, on the loudest field of colour, with its one
 *                 primary action and up to two more things in progress as "Also open" links
 *   ShelfLink     "Worth saying out loud": a link with a "N new" stamp that opens the achievements list in the app's
 *                 Popover (a bottom sheet under 760 px)
 *
 * Nothing here compares the person with an earlier, better number: a score that went down is not shown anywhere, and one
 * that went up is an achievement. Every number is counted in code (deskModel.ts, model.ts). The title's highlighter and the
 * stamps come from the kit in global.css.
 */
import { AnimatePresence } from 'motion/react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Profile } from '../../../shared/types';
import type { PreparePlanSummary } from '../../../shared/prepare';
import { Popover } from '../../components/Popover';
import { STAGE_LABEL } from '../../discover/applications/tracker';
import { dayLabel } from '../../discover/prepare/words';
import { rankRoles } from '../../lib/layout';
import { freeAction } from '../../lib/prompts';
import { NARROW_QUERY, timeAgo, useMediaQuery } from '../../lib/ui';
import { BAND_LABEL } from '../../match/model';
import { PipsLine } from '../../practice/Pips';
import { actions } from '../../store';
import type { PickUp } from './deskModel';
import type { Achievement } from './model';

/* ───────────────────────── pick up where you left off ───────────────────────── */

const PICK_LABEL: Record<PickUp['kind'], string> = {
  practice: 'Practice',
  plan: 'Prepare',
  thread: 'Conversation',
  match: 'Posting match',
  application: 'Your applications',
};

/** The person's first question in a conversation: their own words, cut to one line's worth. */
function firstQuestion(item: Extract<PickUp, { kind: 'thread' }>): string {
  const text = item.thread.messages.find((m) => m.role === 'user')?.text.trim() ?? '';
  return text.length > 140 ? `${text.slice(0, 139).trimEnd()}…` : text;
}

/** A link's title. A conversation started from the ask bar is named by its question; "About you" says nothing. */
function rowTitle(item: PickUp): string {
  if (item.kind !== 'thread' || (item.thread.origin.kind !== 'free' && item.thread.origin.kind !== 'you')) return pickTitle(item);
  const q = firstQuestion(item);
  if (!q) return pickTitle(item);
  return q.length > 48 ? `${q.slice(0, 47).trimEnd()}…` : q;
}

function pickTitle(item: PickUp): string {
  switch (item.kind) {
    case 'practice':
      return item.session.title;
    case 'plan':
      return item.plan.requirement ? `${item.plan.roleTitle} · ${item.plan.requirement}` : item.plan.roleTitle;
    case 'thread':
      return item.thread.origin.title;
    case 'match':
      return item.match.company ? `${item.match.title} at ${item.match.company}` : item.match.title;
    case 'application':
      return `${item.job.job.title} at ${item.job.job.employer}`;
  }
}

function pickAction(item: PickUp): string {
  switch (item.kind) {
    case 'practice':
      return item.session.status === 'active' ? 'Go back in' : 'Read the debrief';
    case 'plan':
      return 'Open the plan';
    case 'thread':
      return 'Carry on';
    case 'match':
      return 'Open the match';
    case 'application':
      return 'Open your applications';
  }
}

const ALSO_MAX = 2;

export function PickTile({
  profile,
  items,
  loading,
  weak,
  now,
  onOpenPlan,
}: {
  profile: Profile;
  items: PickUp[];
  /** The sessions or the plans are still being read. */
  loading: boolean;
  /** Claims whose best verdict is Shaky or Couldn't defend. */
  weak: number;
  now: number;
  onOpenPlan(plan: PreparePlanSummary): void;
}) {
  const top = useMemo(() => rankRoles(profile)[0], [profile]);
  const open = (item: PickUp) => {
    switch (item.kind) {
      case 'practice':
        return actions.openPractice({ screen: item.session.status === 'active' ? 'room' : 'debrief', session: item.session.id });
      case 'plan':
        return onOpenPlan(item.plan);
      case 'thread':
        return actions.selectThread(item.thread.id);
      case 'match':
        return actions.openMatch({ screen: 'match', id: item.match.id });
      case 'application':
        return actions.openDiscover({ tab: 'applications' });
    }
  };
  const [hero, ...others] = items;
  const also = others.slice(0, ALSO_MAX);
  const counts = hero?.kind === 'practice' ? hero.session.counts : undefined;
  const judged = counts ? counts.defended + counts.shaky + counts.couldnt : 0;

  return (
    <section className="t t-pick" aria-labelledby="db-pick-h" aria-busy={loading}>
      <div className="card-head">
        <h2 id="db-pick-h" className="kicker t-ink">
          Pick up where you left off
        </h2>
        <span className="kicker t-ink">{hero ? `${PICK_LABEL[hero.kind]} · ${timeAgo(hero.at, now)}` : loading ? '' : 'nothing yet'}</span>
      </div>

      {!hero && loading && <p className="small">Reading where you left off…</p>}

      {!hero && !loading && (
        <>
          <h3 className="serif t-title">Nothing to pick up yet.</h3>
          <p className="small">
            The last thing you touched waits here.
            {top ? ` ${top.title} is your closest role, at ${top.alignment}%: a fair place to start.` : ''}
          </p>
          <div className="t-actions">
            {top && (
              <button type="button" className="btn btn-primary" onClick={() => actions.openRole(top.id)}>
                Open {top.title}
              </button>
            )}
            <button type="button" className="btn t-btn" onClick={() => actions.openPractice({ screen: 'hub' })}>
              Defend your resume
            </button>
          </div>
        </>
      )}

      {hero && (
        <>
          <h3 className="serif t-title">
            <span className="hl hl-paper">{pickTitle(hero)}</span>
          </h3>
          {hero.kind === 'practice' && (
            <p className="t-detail">
              {counts && judged > 0 && (
                <span className="t-count">
                  <span className="big numeral">{counts.defended}</span>
                  <span className="small">
                    of {judged} defended,
                    <br />
                    out loud
                  </span>
                </span>
              )}
              <PipsLine verdicts={hero.session.verdicts} counts={hero.session.counts} />
              {hero.session.status === 'active' && <span className="stamp stamp-quiet">not finished</span>}
            </p>
          )}
          {hero.kind === 'plan' && <p className="small">Where you stand, the one move, the weeks. Started {dayLabel(hero.plan.createdAt.slice(0, 10))}.</p>}
          {hero.kind === 'thread' && <p className="small">You asked: “{firstQuestion(hero)}”</p>}
          {hero.kind === 'match' && (
            <p className="t-detail">
              <span className="stamp t-stamp">{BAND_LABEL[hero.match.band]}</span>
              <span className="small">Every requirement against your evidence.</span>
            </p>
          )}
          {hero.kind === 'application' && (
            <p className="t-detail">
              <span className="stamp t-stamp">{STAGE_LABEL[hero.job.stage]}</span>
              {hero.job.appliedAt && <span className="small">Applied {dayLabel(hero.job.appliedAt)}</span>}
            </p>
          )}
          <div className="t-bottom">
            <span className="t-actions">
              <button type="button" className="btn btn-primary" onClick={() => open(hero)}>
                {pickAction(hero)}
              </button>
              {hero.kind === 'practice' && weak > 0 && (
                <button type="button" className="btn t-btn" onClick={() => actions.openPractice({ screen: 'redrill' })}>
                  Re-drill {weak}
                </button>
              )}
            </span>
            {also.length > 0 && (
              <span className="fine t-ink t-also">
                Also open:{' '}
                {also.map((item, i) => (
                  <span key={`${item.kind}:${item.id}`}>
                    {i > 0 && ' · '}
                    <button type="button" className="t-link" onClick={() => open(item)}>
                      {rowTitle(item)}
                    </button>
                  </span>
                ))}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

/* ───────────────────────── the shelf, as a link and a popover ───────────────────────── */

const KIND_LABEL: Record<Achievement['kind'], string> = {
  graph: 'Your graph',
  moved: 'Moved',
  applications: 'Applications',
  practice: 'Practice',
  match: 'Posting match',
  prepare: 'Prepare',
  weekly: 'The Weekly',
};

const POP_W = 304;

function dayOf(iso: string | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) : '';
}

/** "Worth saying out loud" with a stamp when something is fresh; pressing it opens what the person has earned. */
export function ShelfLink({ items }: { items: readonly Achievement[] }) {
  const [open, setOpen] = useState(false);
  const narrow = useMediaQuery(NARROW_QUERY);
  const wrap = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const fresh = items.filter((a) => a.fresh).length;

  const model = useMemo(
    () => ({
      key: 'shelf',
      kicker: 'Worth saying out loud',
      title: fresh > 0 ? `${fresh} new this week` : 'Counted, not scored',
      summary: items.length === 0 ? 'Practice, applications and plans add to this list as they happen.' : undefined,
      bullets: items.map((a) => ({ tag: `${KIND_LABEL[a.kind]}${a.at && dayOf(a.at) ? ` · ${dayOf(a.at)}` : ''}${a.fresh ? ' · new' : ''}`, text: `${a.title}. ${a.detail}` })),
      cta: items.length === 0 ? 'Start practising' : 'Ask how to say it',
    }),
    [items, fresh],
  );
  const act = () => {
    setOpen(false);
    if (items.length === 0) actions.openPractice({ screen: 'hub' });
    else actions.openChat(freeAction().origin, `Which of these should I say out loud, and how? ${items.slice(0, 4).map((a) => a.title).join('; ')}.`);
  };
  const close = () => {
    setOpen(false);
    trigger.current?.focus({ preventScroll: true });
  };
  // Escape closes it and hands focus back to the link
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      setOpen(false);
      trigger.current?.focus({ preventScroll: true });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  // under the link, its right edge on the link's right edge
  const place = () => {
    const w = wrap.current;
    return { left: Math.round((w?.offsetWidth ?? POP_W) - POP_W), top: Math.round((w?.offsetHeight ?? 24) + 8) };
  };
  const popover = (sheet: boolean) =>
    open && <Popover key="shelf" model={model} sheet={sheet} place={sheet ? undefined : place} autoFocus="dialog" onClose={close} onAct={act} />;

  return (
    <span className="os-shelf" ref={wrap}>
      <button ref={trigger} type="button" className="os-shelf-btn small" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Worth saying out loud
        {fresh > 0 && (
          <span className="stamp os-shelf-stamp">
            {fresh} new<span className="sr-only"> this week</span>
          </span>
        )}
      </button>
      <AnimatePresence>{!narrow && popover(false)}</AnimatePresence>
      {narrow && createPortal(<AnimatePresence>{popover(true)}</AnimatePresence>, document.body)}
    </span>
  );
}
