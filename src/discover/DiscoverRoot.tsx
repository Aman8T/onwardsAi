/**
 * The Discover section: the header and the screen, wired to the store, the server, the match flow and this browser's
 * storage.
 *
 *   /discover?city=…&remote=0                          the roles of the last search, here
 *   /discover?tab=applications                         the person's own list
 *   /discover?tab=prepare&run=…&job=…                  one role's prep page
 *   /discover?tab=compare&ids=a,b,c                    roles side by side
 *
 * The place comes from the address, else from this browser (`area.ts`); it is sent with each search and each look at the
 * last results and never stored by the server. "See how you fit" reads the role's posting text from the stored search
 * and hands it to the existing match flow, remembering where it came from so the match screen can show the way back
 * and the apply link.
 *
 * "Apply for me" on a role (a card, the role's page, the deck, Compare, Prepare) stays where it was pressed while it is
 * checked (spec 2.1): first the read-only start check (`POST /api/apply/check`, charges nothing), then the full posting
 * match, whose own steps show on the card, with Cancel. Still one match at a time. When the match is done it opens
 * `/match/<id>` with the "How should we apply?" choice expanded (or, when the resume was chosen already, the tailored
 * resume to check, or `/apply/new` with the saved one); a hard requirement that fails, or a check that says no, stays on
 * the card with the reason. Signed out, the button opens the sign-in sheet. A listing whose match this browser already
 * finished reuses it (`listingMatch.ts`, read back with a GET that charges nothing) instead of a new, charged match.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ApplyCheck } from '../../shared/apply';
import type { Profile } from '../../shared/types';
import type { AiJob } from '../../shared/jobsearch';
import { ApplyError, applyApi } from '../apply/api';
import { SIGN_IN_NOTE, blockedReasonOf } from '../apply/ApplyForMe';
import type { ApplyChoice } from '../apply/ApplyForMe';
import { applyPath } from '../apply/route';
import { rememberApplyIntent, useApplications } from '../apply/useApplications';
import { signInSheet, useMe } from '../auth/useMe';
import { MatchApiError, getMatch } from '../match/api';
import { isSampleId, pickProfileId } from '../match/model';
import { getMatchState, matchActions, useMatch } from '../match/useMatch';
import type { MatchState } from '../match/useMatch';
import { actions, useApp } from '../store';
import { readArea, storedPatch, writeArea } from './area';
import type { StoredArea } from './area';
import { DiscoverHeader } from './DiscoverHeader';
import { DiscoverScreen } from './DiscoverScreen';
import { getPosting } from './jobsearch/api';
import { forgetListingMatch, listingMatchFor, rememberListingMatch } from './listingMatch';
import { bindOrigin, writeOrigin } from './origin';
import type { MatchOrigin } from './origin';
import { CHECK_UNREAD, MATCH_BUSY, refusedApply } from './roles/parts';
import type { CardFlow, DiscoverApply } from './roles/parts';
import { discoverPath } from './route';
import type { DiscoverQuery } from './route';
import { checkedWord } from './util';
import { cityFromLocation, useCities } from './useCities';
import '../styles/discover.css';

/** The sample has nothing of the visitor's to match, so "See how you fit" explains instead of starting. */
const NEEDS_ANALYSIS = 'A match compares a posting with your evidence, so your resume needs to be analysed first.';

/** A profile with fewer skills than this has little for the search to go on. */
const THIN_BELOW = 5;

/** The match behind a card's Apply for me stopped before it finished, and said nothing itself. */
const MATCH_STOPPED = 'The match stopped before it finished. Nothing was applied; try again in a moment.';

/** The one role whose Apply for me is being checked right now. */
interface Active {
  job: AiJob;
  runId?: string;
  /** The resume, when it was chosen before the match (a role's page, Prepare). */
  choice?: ApplyChoice;
  stage: 'check' | 'match';
  /** The match was seen running, so a later idle is its end. */
  seen: boolean;
  /** The result on screen before this match started (a new result object is this match's). */
  before?: MatchState['result'];
}

/** What the match state means for the card waiting on it. */
export type MatchOutcome =
  | { kind: 'wait' }
  | { kind: 'blocked'; reason: string }
  | { kind: 'open'; matchId: string }
  | { kind: 'paywall' }
  | { kind: 'stopped'; note: string };

/**
 * The match behind a card's Apply for me, read in code: still running (or not this match yet), done and blocked by a
 * hard requirement (the failing gate's text), done and open for the choice, or stopped (the allowance is used up, or
 * the match said why). A finished match is told apart from the one on screen before by its new result object.
 */
export function matchOutcome(
  m: Pick<MatchState, 'phase' | 'matchId' | 'result' | 'blocked' | 'posting' | 'paywall' | 'notice' | 'gates'>,
  a: Pick<Active, 'seen' | 'before'>,
): MatchOutcome {
  if (m.phase === 'reading') return { kind: 'wait' };
  if (m.phase === 'done' && m.matchId && m.result && m.result !== a.before) {
    if (m.blocked) return { kind: 'blocked', reason: blockedReasonOf(m.posting?.gates ?? [], m.result.gates ?? m.gates ?? []) };
    return { kind: 'open', matchId: m.matchId };
  }
  if (!a.seen || m.phase !== 'idle') return { kind: 'wait' };
  if (m.paywall) return { kind: 'paywall' };
  return { kind: 'stopped', note: m.notice ?? MATCH_STOPPED };
}

/** What a match this browser already finished for a listing says now: open, blocked (with why), gone, or unread. */
type KnownMatch = { kind: 'open' } | { kind: 'blocked'; reason: string } | { kind: 'gone' } | { kind: 'unread' };

/** Read a remembered match back (`GET /api/match/{id}`: no new match, nothing charged). */
async function readKnownMatch(matchId: string): Promise<KnownMatch> {
  try {
    const detail = await getMatch(matchId);
    if (detail.result.blocked) return { kind: 'blocked', reason: blockedReasonOf(detail.posting.gates ?? [], detail.result.gates ?? []) };
    return { kind: 'open' };
  } catch (err) {
    return err instanceof MatchApiError && err.status === 404 ? { kind: 'gone' } : { kind: 'unread' };
  }
}

/** Where the person goes once the match behind Apply for me is done and nothing blocked it. */
function openAfterMatch(matchId: string, a: Active): void {
  bindOrigin(matchId);
  if (!a.choice) {
    // the match screen opens its "How should we apply?" choice (the origin's intent 'apply')
    actions.openMatch({ screen: 'match', id: matchId });
    return;
  }
  const where = { listingId: a.job.id, ...(a.runId ? { runId: a.runId } : {}) };
  rememberApplyIntent({ matchId, resume: a.choice.resume, ...where });
  if (a.choice.resume === 'tailored') actions.openMatch({ screen: 'resume', id: matchId });
  else window.location.assign(applyPath({ screen: 'new', matchId, resume: 'saved', ...where }));
}

export function DiscoverRoot({ profile }: { profile: Profile }) {
  const query = useApp((s) => s.discoverRoute);
  const cities = useCities();
  const { user, loaded, profiles } = useMe();
  const m = useMatch();
  const matchPhase = m.phase;
  const apps = useApplications();
  const [stored, setStored] = useState<StoredArea>(() => readArea(profile.id));
  const [flows, setFlows] = useState<ReadonlyMap<string, CardFlow>>(() => new Map());
  const active = useRef<Active | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const latest = useRef(m);
  latest.current = m;

  // what this browser remembers that the address does not say (the city, "without remote") joins the address once
  useEffect(() => {
    const area = readArea(profile.id);
    setStored(area);
    const patch = storedPatch(query, area);
    if (Object.keys(patch).length > 0) actions.openDiscover(patch, { replace: true });
    // once per profile: later changes are the person's own and write both places
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  const onQuery = (patch: Partial<DiscoverQuery>) => actions.openDiscover(patch);

  const onArea = (choice: { city?: string | null; remote?: boolean }) => {
    const next: StoredArea = { ...stored };
    if (choice.city !== undefined) {
      if (choice.city) next.city = choice.city;
      else delete next.city;
    }
    if (choice.remote !== undefined) {
      if (choice.remote) delete next.remote;
      else next.remote = false;
    }
    writeArea(profile.id, next);
    setStored(readArea(profile.id));
    actions.openDiscover({
      ...(choice.city !== undefined ? { city: choice.city ?? undefined } : {}),
      ...(choice.remote !== undefined ? { remote: choice.remote } : {}),
    });
  };

  const sample = isSampleId(profile.id);
  // the match screen refuses the sample (there is nothing of the visitor's to match), so "See how you fit" explains instead
  const matchProfileId = pickProfileId(profile.id, profiles) ?? (!sample ? profile.id : undefined);
  const signedIn = loaded ? !!user : null;

  /**
   * A full match for a role the search found. With the stored run, the posting's text goes through the same flow as a
   * pasted posting; a role kept from before runs were stored has no run to read, so its posting's address goes to the
   * match flow, which reads the page itself. A refused match leaves the text (or the address) in the paste box.
   * `open` shows the match screen at once ("See how you fit"); Apply for me stays on the card instead.
   */
  const startMatch = async (job: AiJob, runId: string | undefined, opts: { open: boolean; intent?: 'apply' }): Promise<string | undefined> => {
    if (!matchProfileId) return NEEDS_ANALYSIS;
    let text: string | undefined;
    if (runId) {
      try {
        text = (await getPosting(runId, job.id, profile.id)).postingText;
      } catch (err) {
        return err instanceof Error ? err.message : 'Discover is having trouble right now. Please try again in a moment.';
      }
    }
    writeOrigin(originOf(job, runId, opts.intent));
    matchActions.setDraft(text !== undefined ? { text, url: '' } : { text: '', url: job.applyUrl });
    if (opts.open) actions.openMatch({ screen: 'paste' });
    void matchActions.submit(text !== undefined ? { profileId: matchProfileId, text } : { profileId: matchProfileId, url: job.applyUrl }, { signedIn });
    return undefined;
  };

  const seeMatch = (job: AiJob, runId?: string): Promise<string | undefined> => startMatch(job, runId, { open: true });

  /** Where a match from this listing came from, for the match screen's way back and its apply link. */
  function originOf(job: AiJob, runId: string | undefined, intent?: 'apply'): MatchOrigin {
    return {
      listingId: job.id,
      title: job.title,
      employer: job.employer,
      applyUrl: job.applyUrl,
      applyHost: job.applyHost,
      sourceLabel: `Employer's own listing, checked ${checkedWord(job.checkedAt)}`,
      discoverHref: discoverPath(query),
      profileId: profile.id,
      ...(runId ? { runId } : {}),
      ...(intent ? { intent } : {}),
    };
  }

  /* ───────── Apply for me, on the card ───────── */

  const put = (id: string, flow?: CardFlow) =>
    setFlows((prev) => {
      const next = new Map(prev);
      if (flow) next.set(id, flow);
      else next.delete(id);
      return next;
    });

  const end = () => {
    active.current = null;
    setActiveId(null);
  };

  const applyForMe = async (job: AiJob, runId: string | undefined, choice?: ApplyChoice): Promise<void> => {
    if (signedIn !== true) {
      signInSheet.open('account', SIGN_IN_NOTE);
      return;
    }
    if (!matchProfileId) return put(job.id, { kind: 'note', text: NEEDS_ANALYSIS });
    if (latest.current.phase === 'reading' || active.current) return put(job.id, { kind: 'note', text: MATCH_BUSY });
    const status = apps.status;
    const mine: Active = { job, ...(runId ? { runId } : {}), ...(choice ? { choice } : {}), stage: 'check', seen: false };
    active.current = mine;
    setActiveId(job.id);
    put(job.id, { kind: 'checking', phase: 'check' });

    // the read-only start check: nothing is charged, and the (charged) match runs only once it said yes (spec 1 rule 1).
    // A check that cannot be read starts nothing; an expired session opens the sign-in sheet.
    let check: ApplyCheck;
    try {
      check = await applyApi.check({ listingId: job.id, ...(runId ? { runId } : {}) });
    } catch (err) {
      if (active.current !== mine) return; // cancelled
      end();
      if (err instanceof ApplyError && err.status === 401) {
        put(job.id);
        signInSheet.open('account', SIGN_IN_NOTE);
        return;
      }
      put(job.id, { kind: 'note', text: CHECK_UNREAD });
      return;
    }
    if (active.current !== mine) return; // cancelled
    if (!check.offered) {
      end();
      if (check.reason === 'closed') put(job.id, { kind: 'closed' });
      else put(job.id, status ? { kind: 'refused', apply: refusedApply(check, status) } : { kind: 'note', text: CHECK_UNREAD });
      if (check.reason === 'already_applied') apps.refresh();
      return;
    }

    put(job.id, { kind: 'checking', phase: 'match' });
    // a match this browser already finished for the listing is reused: reading it back starts and charges nothing
    const known = listingMatchFor(job.id, profile.id);
    if (known) {
      const now = await readKnownMatch(known);
      if (active.current !== mine) return; // cancelled
      if (now.kind === 'gone') forgetListingMatch(job.id, profile.id);
      else {
        end();
        if (now.kind === 'unread') put(job.id, { kind: 'note', text: CHECK_UNREAD });
        else if (now.kind === 'blocked') put(job.id, { kind: 'refused', apply: { kind: 'off', reason: now.reason } });
        else {
          put(job.id);
          writeOrigin(originOf(job, runId, choice ? undefined : 'apply'));
          openAfterMatch(known, mine);
        }
        return;
      }
    }

    mine.stage = 'match';
    mine.before = latest.current.result;
    const failed = await startMatch(job, runId, { open: false, ...(choice ? {} : { intent: 'apply' as const }) });
    if (active.current !== mine) return;
    if (failed) {
      end();
      put(job.id, { kind: 'note', text: failed });
      return;
    }
    // the match starts reading synchronously; one that did not (no posting text, a sign-in needed) never will, so the
    // card says so instead of waiting for ever with every other card locked
    const now = getMatchState();
    if (now.phase === 'reading') mine.seen = true;
    else {
      end();
      put(job.id, { kind: 'note', text: now.notice ?? MATCH_STOPPED });
    }
  };

  const cancelApply = (job: AiJob) => {
    // the match itself is not stopped from here: it finishes and stays in the person's matches; nothing is applied
    if (active.current?.job.id === job.id) end();
    put(job.id);
  };

  // the match behind the card's Apply for me: follow it, then open the choice, or say why not
  const { phase, matchId, result, blocked, posting, paywall, notice, gates, step } = m;
  useEffect(() => {
    const a = active.current;
    if (!a || a.stage !== 'match') return;
    if (phase === 'reading') a.seen = true;
    const out = matchOutcome({ phase, matchId, result, blocked, posting, paywall, notice, gates }, a);
    if (out.kind === 'wait') return;
    end();
    switch (out.kind) {
      case 'blocked':
        put(a.job.id, { kind: 'refused', apply: { kind: 'off', reason: out.reason } });
        return;
      case 'open':
        put(a.job.id);
        rememberListingMatch(a.job.id, profile.id, out.matchId);
        openAfterMatch(out.matchId, a);
        return;
      case 'paywall':
        // the match allowance is used up: the match screen says so, with the way on
        put(a.job.id);
        actions.openMatch({ screen: 'paste' });
        return;
      case 'stopped':
        put(a.job.id, { kind: 'note', text: out.note });
        return;
    }
    // the match's own fields are the inputs; `put` and `end` only touch state setters and the ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, matchId, result, blocked, posting, paywall, notice, gates]);

  // the steps on the card read the running match: its posting's requirement count and its own sentence
  const liveFlows = useMemo((): ReadonlyMap<string, CardFlow> => {
    const f = activeId ? flows.get(activeId) : undefined;
    if (!activeId || f?.kind !== 'checking' || f.phase !== 'match' || phase !== 'reading') return flows;
    const next = new Map(flows);
    next.set(activeId, { ...f, ...(posting ? { requirements: posting.requirements.length } : {}), ...(step ? { step } : {}) });
    return next;
  }, [flows, activeId, phase, posting, step]);

  const apply: DiscoverApply = {
    status: apps.status,
    signedIn: signedIn === true,
    byListing: apps.byListing,
    flows: liveFlows,
    onApply: (job, runId, choice) => void applyForMe(job, runId, choice),
    onCancel: cancelApply,
  };

  return (
    <div className="dc-view">
      <DiscoverHeader profileId={profile.id} tab={query.tab} onTab={(tab) => onQuery({ tab })} />
      <DiscoverScreen
        profileId={profile.id}
        query={query}
        cities={cities}
        resumeCity={cityFromLocation(profile.person.location, cities)}
        resumeLocation={profile.person.location}
        signedIn={signedIn}
        sample={sample}
        thin={profile.skills.length < THIN_BELOW}
        onQuery={onQuery}
        onArea={onArea}
        onSeeMatch={seeMatch}
        matchLocked={matchPhase === 'reading' || activeId !== null}
        onAnalyse={() => actions.goto('onboarding')}
        onAddProject={() => actions.goto('onboarding')}
        apply={apply}
      />
    </div>
  );
}
