/**
 * The roles, grouped by why each one fits: a serif title, the count, one sentence on why the group matters with the
 * fact it rests on, then the first three rows and "See all N". A role lives in one group and says which others it
 * also fits; it is never counted twice. With few roles (under 12) there is one flat list and no headings.
 */
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { DiscoverResponse, Group, ListingCard } from '../../shared/discover';
import { ListingRow } from './ListingRow';
import { GROUP_ROWS, citeLine, groupCountText } from './model';
import type { TrackedRole } from './tracker';

export const groupDomId = (id: string) => `dc-g-${id.replace(/[^A-Za-z0-9_-]/g, '_')}`;

export interface GroupListProps {
  res: DiscoverResponse;
  flat: boolean;
  areaCity?: string;
  otherCity?: string;
  now: number;
  openId: string | null;
  trackedOf: (id: string) => TrackedRole | undefined;
  expanded: ReadonlySet<string>;
  onExpand: (groupId: string) => void;
  onOpen: (card: ListingCard) => void;
  onSave: (card: ListingCard) => void;
  rowRef: (id: string, el: HTMLLIElement | null) => void;
  titleRef: (id: string, el: HTMLButtonElement | null) => void;
}

export function GroupList(props: GroupListProps) {
  const { res, flat, areaCity, otherCity, now, openId, trackedOf, expanded, onExpand, onOpen, onSave, rowRef, titleRef } = props;
  const byId = useMemo(() => new Map(res.listings.map((l) => [l.id, l])), [res.listings]);
  const groupTitle = useMemo(() => new Map(res.groups.map((g) => [g.id, g.title])), [res.groups]);

  const jump = (groupId: string) => {
    const el = document.getElementById(groupDomId(groupId));
    if (!el) return;
    el.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.focus({ preventScroll: true });
  };

  const row = (card: ListingCard, withAlso: boolean) => (
    <ListingRow
      key={card.id}
      card={card}
      areaCity={areaCity}
      now={now}
      open={openId === card.id}
      tracked={trackedOf(card.id)}
      alsoFits={withAlso ? card.alsoFits.flatMap((id) => (groupTitle.has(id) ? [{ id, title: groupTitle.get(id)! }] : [])) : []}
      rowRef={(el) => rowRef(card.id, el)}
      titleRef={(el) => titleRef(card.id, el)}
      onOpen={() => onOpen(card)}
      onSave={() => onSave(card)}
      onAlso={jump}
    />
  );

  if (flat) {
    return <ul className="dc-rows dc-rows-flat">{res.listings.map((l) => row(l, false))}</ul>;
  }

  return (
    <>
      {res.groups.map((g) => (
        <GroupBlock key={g.id} group={g} res={res} byId={byId} otherCity={otherCity} open={expanded.has(g.id)} onExpand={() => onExpand(g.id)} row={row} />
      ))}
    </>
  );
}

function GroupBlock({ group, res, byId, otherCity, open, onExpand, row }: { group: Group; res: DiscoverResponse; byId: ReadonlyMap<string, ListingCard>; otherCity?: string; open: boolean; onExpand: () => void; row: (card: ListingCard, withAlso: boolean) => ReactNode }) {
  const cards = group.listingIds.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : []));
  const shown = open ? cards : cards.slice(0, GROUP_ROWS);
  return (
    <section className="dc-group" aria-labelledby={groupDomId(group.id)}>
      <header className="dc-group-head">
        <h2 id={groupDomId(group.id)} className="serif dc-group-title" tabIndex={-1}>
          {group.title}
        </h2>
        <span className="dc-muted dc-group-count">{groupCountText(res, group.count, otherCity)}</span>
      </header>
      <p className="dc-reason">{group.reason.text}</p>
      {group.reason.cite && <p className="dc-cite">{citeLine(group.reason.cite)}</p>}
      <ul className="dc-rows">{shown.map((c) => row(c, true))}</ul>
      {cards.length > GROUP_ROWS && (
        <p>
          <button type="button" className="dc-link dc-link-pad" aria-expanded={open} onClick={onExpand}>
            {open ? 'Show fewer' : `See all ${cards.length}`}
          </button>
        </p>
      )}
    </section>
  );
}
