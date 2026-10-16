/**
 * The Discover screen, presentational: everything it shows arrives as props (the place, the person, the profile's
 * state), everything it changes goes out as a callback. `DiscoverRoot` feeds it from the store, the server and the
 * match flow; the dev gallery feeds it from fixtures and scripted streams.
 *
 *   roles           the search: start, searching, results (a grid, or a swipe deck on a phone)
 *   prepare         one role's prep page            (built by `prepare/`)
 *   compare         two or three roles side by side (built by `compare/`)
 *   applications    the roles the person saved      (built by `applications/`)
 *
 * The search itself (the stream, the last results, "not for me") lives in one keyed host above the tabs, so going to a
 * prep page and back never loses a search in progress; the host starts over when the profile, the city or the remote
 * choice changes. Share is a dialog any tab can open.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { DiscoverCity } from '../../shared/discover';
import type { AiJob, ReferralNote, RolePrep, RoleRequest } from '../../shared/jobsearch';
import { NARROW_QUERY, useMediaQuery } from '../lib/ui';
import { ApplicationsView } from './applications/ApplicationsView';
import { useTracker } from './applications/tracker';
import type { TrackedJob } from './applications/tracker';
import { useAvoid } from './jobsearch/avoid';
import { useDiscoverRun } from './jobsearch/useDiscoverRun';
import type { DiscoverRunOptions } from './jobsearch/useDiscoverRun';
import { LayoutContext } from './layout';
import { CompareView } from './compare/CompareView';
import { PrepareScreen } from './prepare/PrepareScreen';
import { RolesView } from './roles/RolesView';
import type { RolesViewProps } from './roles/RolesView';
import { ShareDialog } from './share/ShareDialog';
import type { DiscoverQuery } from './route';
import '../styles/discover.css';

export interface DiscoverScreenProps {
  profileId: string;
  query: DiscoverQuery;
  cities: readonly DiscoverCity[];
  resumeCity?: string;
  resumeLocation?: string;
  signedIn: boolean | null;
  sample: boolean;
  thin: boolean;
  onQuery: (patch: Partial<DiscoverQuery>) => void;
  onArea: RolesViewProps['onArea'];
  onSeeMatch: RolesViewProps['onSeeMatch'];
  matchLocked: boolean;
  onAnalyse: () => void;
  onAddProject: () => void;
  /** Where the stream and the last results come from. The dev gallery passes scripted ones; the app leaves it out. */
  source?: DiscoverRunOptions;
  /** The dev gallery's scripted loaders for the prep page and the referral note; the app leaves them out. */
  loadPrep?: (req: RoleRequest, signal?: AbortSignal) => Promise<RolePrep>;
  loadReferral?: (req: RoleRequest, signal?: AbortSignal) => Promise<ReferralNote>;
  /** The dev gallery shows a phone inside a frame on a desktop. */
  layout?: { phone: boolean; frame: HTMLElement | null };
  initial?: RolesViewProps['initial'] & { compareIds?: string[]; share?: { jobId: string } };
}

export function DiscoverScreen(props: DiscoverScreenProps) {
  const { profileId, query } = props;
  const wide = useMediaQuery(NARROW_QUERY);
  const narrow = props.layout?.phone ?? wide;
  const frame = props.layout?.frame ?? null;
  const layout = useMemo(() => ({ narrow, frame }), [narrow, frame]);

  return (
    <LayoutContext.Provider value={layout}>
      <Host key={`${profileId}|${query.city ?? ''}|${query.remote ? 1 : 0}`} {...props} />
    </LayoutContext.Provider>
  );
}

/** Everything that lives as long as one profile, city and remote choice: the run, what was hidden, what was saved. */
function Host(props: DiscoverScreenProps) {
  const { profileId, query, onQuery } = props;
  const avoid = useAvoid(profileId);
  const tracker = useTracker(profileId);
  const run = useDiscoverRun(profileId, { city: query.city, remote: query.remote, avoid: avoid.prefs }, props.source);
  const [compareIds, setCompareIds] = useState<string[]>(props.initial?.compareIds ?? []);
  const [sharing, setSharing] = useState<{ job: AiJob; runId?: string } | null>(null);
  const [matchNote, setMatchNote] = useState<string | undefined>();

  // the address is the source of truth while roles are being compared
  const idsKey = (query.ids ?? []).join(',');
  useEffect(() => {
    if (query.tab === 'compare') setCompareIds(query.ids ?? []);
    // keyed on the contents, not the array
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.tab, idsKey]);

  // a new tab starts at the top (not on first paint)
  const seenTab = useRef(query.tab);
  useEffect(() => {
    if (seenTab.current === query.tab) return;
    seenTab.current = query.tab;
    setMatchNote(undefined);
    window.scrollTo({ top: 0 });
  }, [query.tab]);

  // the gallery opens the share dialog straight away
  const initialShare = props.initial?.share?.jobId;
  useEffect(() => {
    if (!initialShare) return;
    const job = run.shown?.jobs.find((j) => j.id === initialShare);
    if (job) setSharing((s) => s ?? { job, runId: run.shown?.runId });
  }, [initialShare, run.shown]);

  /** A role by id: from the search on screen, else from what was saved. */
  const jobFor = (id: string | undefined): AiJob | undefined => (id ? (run.shown?.jobs.find((j) => j.id === id) ?? run.liveJobs.find((j) => j.id === id) ?? tracker.get(id)?.job) : undefined);
  const runIdFor = (job: AiJob): string | undefined => (run.shown?.jobs.some((j) => j.id === job.id) ? run.shown.runId : tracker.get(job.id)?.runId);

  const prepare = (job: AiJob, runId: string | undefined) => {
    if (runId) onQuery({ tab: 'prepare', run: runId, job: job.id });
    else setMatchNote('This role was saved before searches were kept, so it has no prep page. Search again to find it.');
  };
  const seeMatch = (job: AiJob, runId: string | undefined) => {
    setMatchNote(undefined);
    void props.onSeeMatch(job, runId).then(setMatchNote);
  };
  const back = () => onQuery({ tab: 'roles' });

  const page = (children: ReactNode) => (
    <main className="dc-main">
      {matchNote && (
        <p className="panel dc-matchnote" role="status">
          {matchNote}
        </p>
      )}
      {children}
    </main>
  );

  let body: ReactNode;
  switch (query.tab) {
    case 'applications':
      body = page(
        <ApplicationsView
          profileId={profileId}
          onPrepare={(item: TrackedJob) => prepare(item.job, item.runId ?? runIdFor(item.job))}
          onSeeMatch={(item: TrackedJob) => seeMatch(item.job, item.runId ?? runIdFor(item.job))}
          onShare={(item: TrackedJob) => setSharing({ job: item.job, runId: item.runId ?? runIdFor(item.job) })}
          onBack={back}
        />,
      );
      break;
    case 'prepare':
      body = page(
        <PrepareScreen
          profileId={profileId}
          runId={query.run ?? ''}
          jobId={query.job ?? ''}
          job={jobFor(query.job)}
          onBack={back}
          onSeeMatch={(job, runId) => seeMatch(job, runId)}
          onShare={(job, runId) => setSharing({ job, runId })}
          loadPrep={props.loadPrep}
        />,
      );
      break;
    case 'compare': {
      const jobs = (query.ids ?? []).flatMap((id) => jobFor(id) ?? []);
      body = page(
        jobs.length === 0 ? (
          <section className="panel dc-note" role="status">
            <div className="dc-note-text">
              <p className="dc-strong">{run.loading ? 'Finding those roles…' : "Those roles aren't in your last results any more."}</p>
            </div>
            <button type="button" className="btn dc-btn" onClick={back}>
              Back to roles
            </button>
          </section>
        ) : (
          <CompareView
            jobs={jobs}
            onRemove={(id) => onQuery({ tab: 'compare', ids: (query.ids ?? []).filter((x) => x !== id) })}
            onClear={() => {
              setCompareIds([]);
              back();
            }}
            onPrepare={(job) => prepare(job, runIdFor(job))}
            onSeeMatch={(job) => seeMatch(job, runIdFor(job))}
            onBack={back}
          />
        ),
      );
      break;
    }
    default:
      body = (
        <RolesView
          profileId={profileId}
          query={query}
          run={run}
          avoid={avoid}
          tracker={tracker}
          cities={props.cities}
          resumeCity={props.resumeCity}
          resumeLocation={props.resumeLocation}
          signedIn={props.signedIn}
          sample={props.sample}
          thin={props.thin}
          compareIds={compareIds}
          onCompareIds={setCompareIds}
          onQuery={onQuery}
          onArea={props.onArea}
          onSeeMatch={props.onSeeMatch}
          onShare={(job, runId) => setSharing({ job, runId })}
          matchLocked={props.matchLocked}
          onAnalyse={props.onAnalyse}
          onAddProject={props.onAddProject}
          initial={props.initial}
        />
      );
  }

  return (
    <>
      <div className="dc-body">{body}</div>
      {sharing && <ShareDialog profileId={profileId} runId={sharing.runId} job={sharing.job} onClose={() => setSharing(null)} loadReferral={props.loadReferral} />}
    </>
  );
}
