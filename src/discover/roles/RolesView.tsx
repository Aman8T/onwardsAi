/**
 * The roles tab of Discover: whatever the search has to show, and the state the person is in.
 *
 *   opening     a quiet outline while the last results are fetched
 *   start       nothing searched yet: the city, the remote choice, "Search with AI"
 *   searching   the live trail, skeletons, and the roles as each one is checked
 *   results     the funnel, the filters, the sections of cards (a grid; a swipe deck on a phone), the details beside them
 *
 * It owns what only this screen knows (which filter is on, which role is open, which match is starting) and is handed
 * the rest: the run, what "not for me" taught, the tracker, the city and what to do when a button asks to leave. A
 * search starts only when a button here is pressed; opening the page never starts one.
 *
 * Apply for me on each role is worked out here (`applyView`, in code) from the host's ATS (`fillableAts`), the person's
 * applications by listing (`useApplications().byListing`, handed in) and what Discover is doing for the role; pressing
 * it goes to `DiscoverRoot`, which runs the start check and the full match on the card.
 *
 * An address that names a role (`/discover?job=<listingId>`, the Weekly paper's link) opens that role's details once
 * the search has it.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { DiscoverCity } from '../../../shared/discover';
import type { AiJob } from '../../../shared/jobsearch';
import { plural } from '../../lib/ui';
import type { Tracker } from '../applications/tracker';
import type { Avoid } from '../jobsearch/avoid';
import type { DiscoverRun } from '../jobsearch/useDiscoverRun';
import { areaWords, isStale, searchedWhen } from '../jobsearch/words';
import { useLayout } from '../layout';
import { COMPARE_MAX, discoverPath } from '../route';
import type { DiscoverQuery } from '../route';
import { CompareTray } from './CompareTray';
import { Deck } from './Deck';
import { Focus } from './Focus';
import { Funnel, NothingFitted } from './Funnel';
import type { RoleCtl } from './JobCard';
import { FailureNote, SampleBanner, ThinNote, isBlocked } from './Notes';
import { applyView } from './parts';
import type { DiscoverApply } from './parts';
import { Sections, Toolbar, countsOf } from './ResultsParts';
import type { Filter } from './ResultsParts';
import { SearchBar } from './SearchBar';
import { LoadingView, SearchingView } from './SearchingView';
import { StartView } from './StartView';

export interface RolesViewProps {
  profileId: string;
  query: DiscoverQuery;
  run: DiscoverRun;
  avoid: Avoid;
  tracker: Tracker;
  cities: readonly DiscoverCity[];
  /** The city the resume names, when it names one the list of cities knows. */
  resumeCity?: string;
  /** The resume's own words for where the person is ("Bengaluru, India"), shown when no city is known. */
  resumeLocation?: string;
  /** null while it is not known whether anyone is signed in. */
  signedIn: boolean | null;
  sample: boolean;
  thin: boolean;
  compareIds: readonly string[];
  onCompareIds: (ids: string[]) => void;
  onQuery: (patch: Partial<DiscoverQuery>) => void;
  /** The place changed: a city (null = back to the one the resume names) and/or whether remote counts. */
  onArea: (choice: { city?: string | null; remote?: boolean }) => void;
  /** Start the match for a role; resolves with a sentence when it could not start, nothing when it did. */
  onSeeMatch: (job: AiJob, runId?: string) => Promise<string | undefined>;
  onShare: (job: AiJob, runId: string) => void;
  /** A posting is already being read, so a second match cannot start. */
  matchLocked: boolean;
  /** Go and analyse a resume (the sample profile, or a profile the server does not have). */
  onAnalyse: () => void;
  /** Add a project or a link (a thin profile, or nothing fitted). */
  onAddProject: () => void;
  /** Apply for me on the roles; absent where nothing can be started (the employer's link stays the action). */
  apply?: DiscoverApply;
  /** The dev gallery opens a state at its most telling moment. */
  initial?: { focusId?: string; popoverId?: string; deckAt?: number; list?: boolean };
}

const FOOTER = "Roles come from employers' own job boards. We never use LinkedIn data.";
const NEEDS_RUN = 'This opens once the search has finished. Search again to finish it.';

export function RolesView(p: RolesViewProps) {
  const { narrow } = useLayout();
  const { run, avoid, tracker, query } = p;
  const data = run.shown;
  const runId = data?.runId;

  const [filter, setFilter] = useState<Filter>('all');
  const [showHidden, setShowHidden] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(p.initial?.focusId ?? null);
  const [list, setList] = useState(p.initial?.list ?? false);
  const [matching, setMatching] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ id: string; text: string } | null>(null);
  const stopRef = useRef<HTMLButtonElement>(null);

  const now = useMemo(() => Date.now(), [data?.createdAt, data?.jobs.length, run.liveJobs.length, run.running]);

  /* ───────── may a search start? ───────── */

  const failure = run.failure;
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    setWaited(false);
    if (failure?.status !== 429 || failure.retryAfter === undefined) return;
    const t = window.setTimeout(() => setWaited(true), failure.retryAfter * 1000);
    return () => window.clearTimeout(t);
  }, [failure]);
  const limited = failure?.status === 429 && failure.retryAfter !== undefined && !waited;
  const blocked = isBlocked(failure) || limited;
  const blockedWhy = limited ? 'The limit has been reached for now' : failure?.status === 404 ? 'Your profile is not on this server' : 'Search with AI is not available right now';

  const search = () => {
    setNotice(null);
    setFocusId(null);
    setShowHidden(false);
    run.start();
  };

  /* ───────── the roles on screen ───────── */

  const all = data?.jobs ?? [];
  const hiddenAll = all.filter((j) => avoid.hidden.has(j.id));
  const visible = all.filter((j) => !avoid.hidden.has(j.id));
  // roles hidden during this visit stay as slim rows (Undo is right there); "Show" brings back the older ones too
  const fresh = new Set(hiddenAll.filter((j) => avoid.reasonOf(j.id) !== undefined).map((j) => j.id));
  const revealed = showHidden ? new Set(hiddenAll.map((j) => j.id)) : fresh;
  const rows = all.filter((j) => !avoid.hidden.has(j.id) || revealed.has(j.id));
  const counts = countsOf(visible, tracker.isSaved);
  const keep = (j: AiJob) => filter === 'all' || (filter === 'saved' ? tracker.isSaved(j.id) : j.fit === filter);
  const shown = rows.filter(keep);

  // a filter with nothing left in it (everything hidden) steps back to All
  useEffect(() => {
    if (filter !== 'all' && filter !== 'saved' && counts[filter] === 0) setFilter('all');
  }, [filter, counts]);

  const focusJob = focusId ? visible.find((j) => j.id === focusId) : undefined;
  // a role that is gone (hidden, or a newer search without it) closes; before the roles arrive nothing is gone yet
  useEffect(() => {
    if (focusId && data && !focusJob) setFocusId(null);
  }, [focusId, focusJob, data]);

  // an address that names a role opens its details once the search has it (once per role named)
  const named = query.tab === 'roles' ? query.job : undefined;
  const openedNamed = useRef<string | null>(null);
  useEffect(() => {
    if (!named || openedNamed.current === named || !data) return;
    // only once the role is there: a search still streaming may bring it in a later snapshot
    if (!visible.some((j) => j.id === named)) return;
    openedNamed.current = named;
    setFocusId(named);
    // the roles are read once they arrive
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [named, data]);

  const compared = p.compareIds.flatMap((id) => all.find((j) => j.id === id) ?? []);

  // a role saved while the search was still running learns which search it came from once that finishes
  useEffect(() => {
    if (!runId) return;
    for (const t of tracker.items) {
      if (t.runId || t.stage !== 'saved' || t.note || !all.some((j) => j.id === t.job.id)) continue;
      tracker.remove(t.job.id);
      tracker.save(t.job, runId);
    }
    // only when a search finishes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // focus follows the person: Stop when a search starts, the page's heading when it ends
  const wasRunning = useRef(run.running);
  useEffect(() => {
    if (wasRunning.current === run.running) return;
    wasRunning.current = run.running;
    const frame = requestAnimationFrame(() => {
      const at = document.activeElement;
      if (at && at !== document.body) return;
      if (run.running) stopRef.current?.focus({ preventScroll: true });
      else document.querySelector<HTMLElement>('[data-dc-h1]')?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [run.running]);

  /* ───────── what the buttons on a role do ───────── */

  const say = (job: AiJob, text: string) => setNotice({ id: job.id, text });

  const seeMatch = async (job: AiJob) => {
    setNotice(null);
    if (p.matchLocked || matching !== null) return say(job, 'A posting is already being read. Try again in a moment.');
    if (!runId) return say(job, NEEDS_RUN);
    setMatching(job.id);
    const text = await p.onSeeMatch(job, runId);
    setMatching(null);
    setNotice(text ? { id: job.id, text } : null);
  };

  const applyForMe = (job: AiJob, choice?: Parameters<DiscoverApply['onApply']>[2]) => {
    setNotice(null);
    if (!p.apply) return;
    if (!runId || run.running) return say(job, NEEDS_RUN);
    p.apply.onApply(job, runId, choice);
  };

  const toggleCompare = (job: AiJob) => {
    const has = p.compareIds.includes(job.id);
    if (!has && p.compareIds.length >= COMPARE_MAX) return;
    p.onCompareIds(has ? p.compareIds.filter((id) => id !== job.id) : [...p.compareIds, job.id]);
  };

  const ctlOf = (job: AiJob): RoleCtl => {
    const view = p.apply ? applyView(job, p.apply) : undefined;
    return {
      runReady: !!runId && !run.running,
      prepareHref: runId ? discoverPath({ ...query, tab: 'prepare', run: runId, job: job.id }) : undefined,
      saved: tracker.isSaved(job.id),
      comparing: p.compareIds.includes(job.id),
      compareFull: p.compareIds.length >= COMPARE_MAX && !p.compareIds.includes(job.id),
      matchBusy: matching === job.id,
      matchLocked: p.matchLocked || (matching !== null && matching !== job.id),
      notice: notice?.id === job.id ? notice.text : view?.note,
      ...(tracker.get(job.id)?.note ? { note: tracker.get(job.id)!.note } : {}),
      onOpen: () => setFocusId(job.id),
      onPrepare: () => (runId && !run.running ? p.onQuery({ tab: 'prepare', run: runId, job: job.id }) : say(job, NEEDS_RUN)),
      onSeeMatch: () => void seeMatch(job),
      onToggleSave: () => tracker.toggle(job, runId),
      onToggleCompare: () => toggleCompare(job),
      onShare: () => (runId && !run.running ? p.onShare(job, runId) : say(job, NEEDS_RUN)),
      onHide: (reason) => avoid.hide(job, reason),
      onUnhide: () => avoid.undo(job),
      ...(view ? { apply: view.apply } : {}),
      ...(view?.progress ? { applyProgress: view.progress } : {}),
      ...(view?.closed ? { applyClosed: true } : {}),
      onApplyForMe: (choice) => applyForMe(job, choice),
      onCancelApply: () => p.apply?.onCancel(job),
      ...(p.apply?.signInNext ? { applySignInNext: p.apply.signInNext } : {}),
    };
  };

  /* ───────── the words ───────── */

  const cityName = query.city ?? data?.city ?? p.resumeCity ?? p.resumeLocation;
  const citySource: 'resume' | 'you' | 'none' = query.city ? 'you' : cityName ? 'resume' : 'none';
  const where = areaWords(cityName, query.remote);
  const cityProps = { name: cityName, source: citySource, cities: p.cities, picked: query.city, resumeCity: p.resumeCity, onPick: (city: string | undefined) => p.onArea({ city: city ?? null }) };

  const sample = p.sample ? <SampleBanner onAnalyse={p.onAnalyse} /> : null;
  const foot = <footer className="dc-footer">{FOOTER}</footer>;

  /* ───────── opening ───────── */

  if (run.loading) {
    return (
      <main className="dc-main">
        <DelayedLoading />
      </main>
    );
  }

  /* ───────── searching ───────── */

  if (run.running) {
    return (
      <main className="dc-main">
        {sample}
        <SearchingView where={where} steps={run.steps} jobs={run.liveJobs} now={now} ctlOf={ctlOf} onStop={run.stop} stopRef={stopRef} compact={narrow} />
        {foot}
      </main>
    );
  }

  /* ───────── nothing searched yet ───────── */

  if (!data) {
    const notes: ReactNode = (
      <>
        {failure && <FailureNote failure={failure} hasResults={false} found={0} onRetry={search} onAnalyse={p.onAnalyse} />}
        {p.thin && !p.sample && <ThinNote onAdd={p.onAddProject} />}
      </>
    );
    return (
      <main className="dc-main">
        {sample}
        <StartView
          {...cityProps}
          citySource={citySource}
          cityName={cityName}
          remote={query.remote}
          onRemote={(on) => p.onArea({ remote: on })}
          onSearch={search}
          blocked={blocked}
          blockedWhy={blockedWhy}
          signedIn={p.signedIn}
          notes={notes}
          apps={tracker.items}
          appsHref={discoverPath({ ...query, tab: 'applications' })}
          onApps={() => p.onQuery({ tab: 'applications' })}
        />
        {foot}
      </main>
    );
  }

  /* ───────── results ───────── */

  const n = all.length;
  const stale = !data.partial && isStale(data.createdAt, now);
  const when = searchedWhen(data.createdAt, now);
  const status =
    data.partial === 'stopped'
      ? `You stopped the search · ${plural(n, 'role')} had been found by then`
      : data.partial === 'failed'
        ? `The search stopped early · ${plural(n, 'role')} found`
        : stale
          ? `Searched ${when} · some roles may have closed since`
          : `Searched ${when} · ${plural(n, 'role')}`;
  const sub = data.partial || stale ? `${where}${data.partial ? '' : ` · ${plural(n, 'role')}`}` : undefined;

  const bar = (
    <SearchBar
      {...cityProps}
      remote={query.remote}
      onRemote={(on) => p.onArea({ remote: on })}
      status={status}
      sub={sub}
      label="Search again"
      blocked={blocked}
      blockedWhy={blockedWhy}
      onSearch={search}
      hint="About 20 seconds"
    />
  );
  const failureNote = failure ? <FailureNote failure={failure} hasResults found={data.partial ? n : 0} onRetry={search} onAnalyse={p.onAnalyse} /> : null;

  // a phone steps through the roles one at a time; "Show as a list" turns that into the grid's single column
  if (narrow && !list && n > 0) {
    const deckJobs = all.filter((j) => !avoid.hidden.has(j.id) || fresh.has(j.id));
    return (
      <>
        <main className="dc-main is-deck">
          {sample}
          {failureNote}
          <Deck
            jobs={deckJobs}
            hidden={fresh}
            now={now}
            ctlOf={ctlOf}
            savedCount={counts.saved}
            where={`${where} · ${status}`}
            onDetails={(job) => setFocusId(job.id)}
            onList={() => setList(true)}
            onReviewSaved={() => {
              setFilter('saved');
              setList(true);
            }}
            onSearchAgain={search}
            searchBlocked={blocked}
            startAt={p.initial?.deckAt}
          />
          {foot}
        </main>
        {focusJob && <Focus job={focusJob} now={now} ctl={ctlOf(focusJob)} onClose={() => setFocusId(null)} />}
      </>
    );
  }

  const title = n === 0 ? undefined : `${plural(n, 'role')} you might not have found`;
  const lede = data.partial
    ? 'This search was stopped before it finished, so these are the roles found so far. Search again to finish it.'
    : "Claude searched employers' own job sites, read their boards, and checked each posting against your evidence. Every role here was listed on its employer's board when the search ran.";
  const dim = !!focusJob && !narrow;

  return (
    <>
      <main className={`dc-main${dim ? ' is-dim' : ''}`} inert={dim || undefined}>
        {sample}
        {title && (
          <section className="dc-hero">
            <div className="kicker">Discover</div>
            <h1 className="serif dc-title" data-dc-h1 tabIndex={-1}>
              {title}
            </h1>
            <p className="dc-lede">{lede}</p>
          </section>
        )}
        {failureNote}
        {bar}

        {n === 0 ? (
          <NothingFitted funnel={data.funnel} note={data.note} trail={data.trail}>
            {!query.remote && (
              <button type="button" className="btn dc-btn" onClick={() => p.onArea({ remote: true })}>
                Try including remote roles
              </button>
            )}
            <button type="button" className="btn dc-btn" onClick={p.onAddProject}>
              Add a project to your profile
            </button>
          </NothingFitted>
        ) : (
          <>
            <Toolbar
              filter={filter}
              onFilter={setFilter}
              counts={counts}
              hiddenCount={hiddenAll.length}
              showHidden={showHidden}
              onShowHidden={setShowHidden}
              anyAvoided={avoid.prefs.employers.length > 0 || avoid.prefs.noStepUp}
              onResetHidden={() => {
                avoid.reset();
                setShowHidden(false);
              }}
              onDeck={narrow ? () => setList(false) : undefined}
            />
            {shown.length === 0 ? (
              <p className="dc-muted dc-empty" role="status">
                {filter === 'saved' ? 'Nothing is saved from these results yet.' : 'No roles in this view.'}
              </p>
            ) : (
              <Sections rows={shown} hidden={revealed} now={now} ctlOf={ctlOf} reasonOf={avoid.reasonOf} onUnhide={(job) => avoid.undo(job)} popoverId={p.initial?.popoverId} />
            )}
            <Funnel funnel={data.funnel} trail={data.trail} note={data.note} />
          </>
        )}

        <CompareTray jobs={compared} onClear={() => p.onCompareIds([])} onCompare={() => p.onQuery({ tab: 'compare', ids: [...p.compareIds] })} />
        {foot}
      </main>
      {focusJob && <Focus job={focusJob} now={now} ctl={ctlOf(focusJob)} onClose={() => setFocusId(null)} />}
    </>
  );
}

/** The outline of the page, shown only if the last results take more than a moment to arrive. */
function DelayedLoading() {
  const [late, setLate] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setLate(true), 150);
    return () => window.clearTimeout(t);
  }, []);
  return late ? <LoadingView /> : null;
}

