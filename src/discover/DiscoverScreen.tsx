/**
 * The Discover screen, presentational: everything it shows arrives as props (the response, the lens, the places),
 * everything it changes goes out as a callback. `DiscoverRoot` feeds it from the store and the server; the dev
 * gallery feeds it from fixtures. The saved roles and their tracker are the one thing it reads for itself, because
 * they live in this browser and nowhere else.
 *
 *   title · where and how wide · lanes · (asking for an area) · groups of roles · companies · coverage · footer
 *
 * One column, as wide as the reading allows. The card for a role opens beside its row on wide screens and as a
 * bottom sheet under 760 px.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { DiscoverCity, DiscoverReach, ListingCard } from '../../shared/discover';
import { Applications } from './Applications';
import type { ListingCheck } from './Applications';
import { CompaniesLane } from './Companies';
import { ContextBar, LocalityPrompt, NoCity } from './ContextBar';
import type { AreaChoice } from './ContextBar';
import { GroupList } from './GroupList';
import { BreadthLens, LaneTabs, ReachLens } from './Lenses';
import { CardHost } from './ListingCard';
import { FLAT_BELOW, headline, isAllWeak, isFirstRefresh, isThin, widenAction } from './model';
import { CoverageNote, EmptyState, ErrorState, FirstRefresh, Footer, LoadingRows, ThinBanner, WeakBanner } from './States';
import { WhyOrder } from './WhyOrder';
import type { DiscoverQuery } from './route';
import type { DiscoverState } from './useDiscover';
import { useTracker } from './tracker';
import '../styles/discover.css';

export interface DiscoverScreenProps {
  profileId: string;
  query: DiscoverQuery;
  state: Pick<DiscoverState, 'status' | 'data' | 'error' | 'waited' | 'reload'>;
  cities: readonly DiscoverCity[];
  /** The person chose "Show roles across the city for now" when asked for an area. */
  localityDeclined: boolean;
  onQuery: (patch: Partial<DiscoverQuery>) => void;
  onArea: (choice: AreaChoice) => void;
  onDeclineLocality: () => void;
  /** Open the paste screen. */
  onPaste: () => void;
  /** Thin profile: add a project or a link. */
  onAddProject: () => void;
  /** Start a full match from a listing; resolves with a sentence when it could not start, nothing when it did. */
  onSeeMatch: (card: ListingCard) => Promise<string | undefined>;
  /** A posting is already being read, so a second cannot start. */
  matchLocked: boolean;
  onOpenMatch: (matchId: string) => void;
  check?: (id: string) => Promise<ListingCheck>;
  /** Something that stops Discover for this profile, shown in place of the list. */
  blocked?: ReactNode;
  /** A line above the list that does not stop it (the sample profile says whose roles these are). */
  banner?: ReactNode;
  /** Open this listing's card on first paint (the dev gallery's `?open=`). */
  initialOpenId?: string;
}

export function DiscoverScreen(props: DiscoverScreenProps) {
  const { profileId, query, state, cities, localityDeclined, onQuery, onArea, onDeclineLocality, onPaste, onAddProject, onSeeMatch, matchLocked, onOpenMatch, check, blocked, initialOpenId } = props;
  const { status, data: res, error, waited, reload } = state;
  const tracker = useTracker(profileId);

  const [openId, setOpenId] = useState<string | null>(initialOpenId ?? null);
  const openRef = useRef<string | null>(null);
  openRef.current = openId;
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [editing, setEditing] = useState(false);
  const [focusField, setFocusField] = useState<'otherCity' | undefined>();
  const [matching, setMatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ id: string; text: string } | null>(null);

  const rowEls = useRef(new Map<string, HTMLLIElement>());
  const titleEls = useRef(new Map<string, HTMLButtonElement>());
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);

  const now = useMemo(() => Date.now(), [res]);
  const first = res !== null && isFirstRefresh(res);
  const dim = status === 'loading' && res !== null;

  // a card whose listing is no longer in the answer closes
  useEffect(() => {
    if (openId && res && !res.listings.some((l) => l.id === openId)) setOpenId(null);
  }, [res, openId]);

  // a new screen (roles / your applications): the heading takes focus, but not on first paint
  const seenTab = useRef(query.tab);
  useEffect(() => {
    if (seenTab.current === query.tab) return;
    seenTab.current = query.tab;
    window.scrollTo({ top: 0 });
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-dc-h1]')?.focus({ preventScroll: true }));
    return () => cancelAnimationFrame(frame);
  }, [query.tab]);

  const dismiss = useCallback((restoreFocus: boolean) => {
    const id = openRef.current;
    setOpenId(null);
    if (restoreFocus && id) requestAnimationFrame(() => titleEls.current.get(id)?.focus());
  }, []);

  const openCard = (card: ListingCard) => setOpenId((cur) => (cur === card.id ? null : card.id));
  const toggleSave = (card: ListingCard) => {
    const held = tracker.get(card.id);
    if (!held) tracker.save(card, res?.area.city);
    else if (held.status === 'saved') tracker.remove(card.id);
  };
  const seeMatch = async (card: ListingCard) => {
    setNotice(null);
    setMatching(card.id);
    const text = await onSeeMatch(card);
    setMatching(null);
    if (text) setNotice({ id: card.id, text });
  };

  const card = res && openId ? (res.listings.find((l) => l.id === openId) ?? null) : null;

  /* ───────── the applications tab ───────── */
  if (query.tab === 'applications') {
    return (
      <main className="dc-main">
        <Applications tracker={tracker} check={check} onOpenMatch={onOpenMatch} onBack={() => onQuery({ tab: 'roles' })} />
      </main>
    );
  }

  /* ───────── the title ───────── */
  const title = blocked
    ? 'Roles near you that fit'
    : !res
      ? status === 'error'
        ? 'Roles near you that fit'
        : 'Finding roles that fit you'
      : first
        ? "Reading employers' job boards"
        : !res.area.city
          ? 'Where should we look?'
          : headline(res, query.otherCity);

  const hasCity = !!res?.area.city && !first;
  const localities = cities.find((c) => c.name === res?.area.city)?.localities ?? [];

  return (
    <main className="dc-main">
      <section className="dc-intro">
        <p className="kicker">Discover · roles near you that fit</p>
        <h1 className="serif dc-title" data-dc-h1 tabIndex={-1}>
          {title}
        </h1>
        <p className="dc-lede">Built from your profile and your next roles. Nothing here was written by the postings.</p>
      </section>

      {blocked}
      {!blocked && props.banner}

      {!blocked && res && hasCity && (
        <ContextBar
          area={res.area}
          otherCity={query.otherCity}
          cities={cities}
          editing={editing}
          focusField={focusField}
          onEditing={(open, focus) => {
            setEditing(open);
            setFocusField(open ? focus : undefined);
          }}
          onSave={onArea}
        >
          {res.lane === 'near' && <ReachLens stops={res.reachStops} value={res.reach} hasLocality={!!res.area.locality} onPick={(reach: DiscoverReach) => onQuery({ reach })} />}
          {res.lane !== 'city' && <BreadthLens stops={res.breadthStops} value={res.breadth} onPick={(breadth) => onQuery({ breadth })} />}
          <WhyOrder signals={res.signals} order={res.order} onOrder={(order) => onQuery({ order })} />
        </ContextBar>
      )}

      {!blocked && res && hasCity && (
        <div className="dc-lane-row">
          <LaneTabs
            lane={res.lane}
            counts={res.laneCounts}
            otherCity={query.otherCity}
            onPick={(lane) => onQuery({ lane })}
            onAddCity={() => {
              setEditing(true);
              setFocusField('otherCity');
            }}
          />
          {res.lane === 'near' && res.suggestion && res.listings.length > 0 && (
            <div className="dc-suggest">
              <span className="dc-muted">{res.suggestion}</span>
              {(() => {
                const widen = widenAction(res);
                return widen ? (
                  <button type="button" className="btn btn-sm dc-btn-sm" onClick={() => onQuery({ reach: widen.reach })}>
                    {widen.label}
                  </button>
                ) : null;
              })()}
            </div>
          )}
        </div>
      )}

      {!blocked && !res && status === 'loading' && <LoadingRows />}
      {!blocked && status === 'error' && <ErrorState message={error} onRetry={reload} />}

      {!blocked && res && first && status !== 'error' && <FirstRefresh note={res.coverage.note} waited={waited} onCheck={reload} />}

      {!blocked && res && !first && !res.area.city && status !== 'error' && <NoCity resumeText={res.area.resumeText} cities={cities} onPick={(city) => onArea({ city })} />}

      {!blocked && res && hasCity && status !== 'error' && (
        <>
          {res.lane === 'near' && !res.area.locality && !localityDeclined && !editing && (
            <LocalityPrompt
              city={res.area.city!}
              cityFrom={res.area.cityFrom}
              localities={localities}
              onPick={(locality) => onArea({ city: res.area.city, locality, otherCity: query.otherCity })}
              onMore={() => setEditing(true)}
              onSkip={onDeclineLocality}
            />
          )}

          {isThin(res.listings) && <ThinBanner onAdd={onAddProject} />}
          {!isThin(res.listings) && isAllWeak(res.listings) && <WeakBanner res={res} />}

          <p className="sr-only" role="status">
            {status === 'loading' ? 'Updating the list' : `${res.total} ${res.total === 1 ? 'role' : 'roles'} shown`}
          </p>

          <div className={`dc-results${dim ? ' is-dim' : ''}`} ref={setContainerEl} aria-busy={dim || undefined}>
            {res.listings.length === 0 ? (
              <EmptyState
                res={res}
                otherCity={query.otherCity}
                onWiden={(reach) => onQuery({ reach: reach as DiscoverReach })}
                onBreadth={(breadth) => onQuery({ breadth: breadth as DiscoverQuery['breadth'] })}
                onRemote={() => onQuery({ lane: 'remote' })}
                onPaste={onPaste}
              />
            ) : (
              <GroupList
                res={res}
                flat={res.total < FLAT_BELOW || res.groups.length === 0}
                areaCity={res.area.city}
                otherCity={query.otherCity}
                now={now}
                openId={openId}
                trackedOf={tracker.get}
                expanded={expanded}
                onExpand={(id) =>
                  setExpanded((cur) => {
                    const next = new Set(cur);
                    if (!next.delete(id)) next.add(id);
                    return next;
                  })
                }
                onOpen={openCard}
                onSave={toggleSave}
                rowRef={(id, el) => (el ? rowEls.current.set(id, el) : rowEls.current.delete(id))}
                titleRef={(id, el) => (el ? titleEls.current.set(id, el) : titleEls.current.delete(id))}
              />
            )}
            <CardHost
              card={card}
              areaCity={res.area.city}
              now={now}
              rowEl={card ? (rowEls.current.get(card.id) ?? null) : null}
              containerEl={containerEl}
              onDismiss={dismiss}
              tracked={card ? tracker.get(card.id) : undefined}
              onSave={() => card && toggleSave(card)}
              onSeeMatch={() => card && void seeMatch(card)}
              matchBusy={!!card && matching === card.id}
              matchLocked={matchLocked || (matching !== null && matching !== card?.id)}
              notice={card && notice?.id === card.id ? notice.text : undefined}
            />
          </div>

          {res.lane === 'near' && <CompaniesLane companies={res.companies} />}
          {res.coverage.note && <CoverageNote note={res.coverage.note} onPaste={onPaste} />}
        </>
      )}

      <Footer coverage={res?.coverage ?? { employers: 0, listings: 0 }} refreshedAt={res?.refreshedAt} />
    </main>
  );
}
