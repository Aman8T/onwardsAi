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
 */
import { useEffect, useState } from 'react';
import type { Profile } from '../../shared/types';
import type { AiJob } from '../../shared/jobsearch';
import { useMe } from '../auth/useMe';
import { isSampleId, pickProfileId } from '../match/model';
import { matchActions, useMatch } from '../match/useMatch';
import { actions, useApp } from '../store';
import { readArea, storedPatch, writeArea } from './area';
import type { StoredArea } from './area';
import { DiscoverHeader } from './DiscoverHeader';
import { DiscoverScreen } from './DiscoverScreen';
import { getPosting } from './jobsearch/api';
import { writeOrigin } from './origin';
import { discoverPath } from './route';
import type { DiscoverQuery } from './route';
import { checkedWord } from './util';
import { cityFromLocation, useCities } from './useCities';
import '../styles/discover.css';

/** The sample has nothing of the visitor's to match, so "See how you fit" explains instead of starting. */
const NEEDS_ANALYSIS = 'A match compares a posting with your evidence, so your resume needs to be analysed first.';

/** A profile with fewer skills than this has little for the search to go on. */
const THIN_BELOW = 5;

export function DiscoverRoot({ profile }: { profile: Profile }) {
  const query = useApp((s) => s.discoverRoute);
  const cities = useCities();
  const { user, loaded, profiles } = useMe();
  const matchPhase = useMatch().phase;
  const [stored, setStored] = useState<StoredArea>(() => readArea(profile.id));

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
   */
  const seeMatch = async (job: AiJob, runId?: string): Promise<string | undefined> => {
    if (!matchProfileId) return NEEDS_ANALYSIS;
    let text: string | undefined;
    if (runId) {
      try {
        text = (await getPosting(runId, job.id, profile.id)).postingText;
      } catch (err) {
        return err instanceof Error ? err.message : 'Discover is having trouble right now. Please try again in a moment.';
      }
    }
    writeOrigin({
      listingId: job.id,
      title: job.title,
      employer: job.employer,
      applyUrl: job.applyUrl,
      applyHost: job.applyHost,
      sourceLabel: `Employer's own listing, checked ${checkedWord(job.checkedAt)}`,
      discoverHref: discoverPath(query),
      profileId: profile.id,
    });
    matchActions.setDraft(text !== undefined ? { text, url: '' } : { text: '', url: job.applyUrl });
    actions.openMatch({ screen: 'paste' });
    void matchActions.submit(text !== undefined ? { profileId: matchProfileId, text } : { profileId: matchProfileId, url: job.applyUrl }, { signedIn });
    return undefined;
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
        matchLocked={matchPhase === 'reading'}
        onAnalyse={() => actions.goto('onboarding')}
        onAddProject={() => actions.goto('onboarding')}
      />
    </div>
  );
}
