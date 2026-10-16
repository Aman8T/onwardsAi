/**
 * The Discover section: the header and the screen, wired to the store, the server, the match flow and this
 * browser's storage.
 *
 *   /discover?city=…&locality=…&reach=…&breadth=…&lane=…&order=…&otherCity=…   the lens, in the address
 *   /discover?tab=applications                                                 the person's own list
 *
 * The area comes from the address, else from this browser (`area.ts`); it is sent with each request and never stored
 * by the server. "See full match" reads the listing's posting text from the server and hands it to the existing
 * match flow, remembering where it came from so the match screen can show the way back and the apply link.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Profile } from '../../shared/types';
import type { ListingCard } from '../../shared/discover';
import { useMe } from '../auth/useMe';
import { isSampleId, pickProfileId } from '../match/model';
import { matchActions, useMatch } from '../match/useMatch';
import { actions, useApp } from '../store';
import { getListing } from './api';
import { readArea, withStoredArea, writeArea } from './area';
import type { StoredArea } from './area';
import type { AreaChoice } from './ContextBar';
import { DiscoverHeader } from './DiscoverHeader';
import { DiscoverScreen } from './DiscoverScreen';
import { writeOrigin } from './origin';
import { discoverPath } from './route';
import type { DiscoverQuery } from './route';
import { useCities, useDiscover } from './useDiscover';
import '../styles/discover.css';

export function DiscoverRoot({ profile }: { profile: Profile }) {
  const route = useApp((s) => s.discoverRoute);
  const cities = useCities();
  const { user, loaded, profiles } = useMe();
  const matchPhase = useMatch().phase;

  // the area this browser remembers for this profile
  const [stored, setStored] = useState<StoredArea>(() => readArea(profile.id));
  useEffect(() => setStored(readArea(profile.id)), [profile.id]);
  const query = useMemo(() => withStoredArea(route, stored), [route, stored]);

  // the address carries the area too: a link or a reload reads the same screen
  useEffect(() => {
    if (query.city !== route.city || query.locality !== route.locality || query.otherCity !== route.otherCity) {
      actions.openDiscover({ city: query.city, locality: query.locality, otherCity: query.otherCity }, { replace: true });
    }
  }, [query, route]);

  // the sample is how most people first see Discover: it runs, and says whose roles these are
  const sample = isSampleId(profile.id);
  const state = useDiscover(profile.id, query, query.tab === 'roles');

  const onQuery = useCallback((patch: Partial<DiscoverQuery>) => actions.openDiscover(patch), []);

  const onArea = (choice: AreaChoice) => {
    const next: StoredArea = { ...choice, ...(stored.localityDeclined && !choice.locality ? { localityDeclined: true } : {}) };
    writeArea(profile.id, next);
    setStored(readArea(profile.id));
    actions.openDiscover({
      city: choice.city,
      locality: choice.locality,
      otherCity: choice.otherCity,
      // the whole city stays the default: few employers state a neighbourhood, so a radius starts nearly empty;
      // the distance stops show their counts and the person narrows when they want to
      reach: choice.locality ? query.reach : undefined,
    });
  };

  const onDeclineLocality = () => {
    const next = { ...stored, localityDeclined: true };
    writeArea(profile.id, next);
    setStored(readArea(profile.id));
  };

  // a full match from a listing: the posting text goes through the same flow as a pasted posting
  // the match screen refuses the sample (there is nothing of the visitor's to match), so the card explains instead
  const matchProfileId = pickProfileId(profile.id, profiles) ?? (!sample ? profile.id : undefined);
  const seeMatch = async (card: ListingCard): Promise<string | undefined> => {
    if (!matchProfileId) return 'A match compares a posting with your evidence, so your resume needs to be analysed first.';
    let detail;
    try {
      detail = await getListing(card.id, profile.id);
    } catch (err) {
      return err instanceof Error ? err.message : 'Discover is having trouble right now. Please try again in a moment.';
    }
    if (!detail) return "That role is no longer on the employer's board.";
    writeOrigin({
      listingId: card.id,
      title: card.title,
      employer: card.employer,
      applyUrl: card.applyUrl,
      applyHost: card.applyHost,
      sourceLabel: card.sourceLabel,
      discoverHref: discoverPath(query),
      profileId: profile.id,
    });
    // if the match is refused (no free posting left, a sign-in needed), the posting waits in the paste box
    matchActions.setDraft({ text: detail.postingText, url: '' });
    actions.openMatch({ screen: 'paste' });
    void matchActions.submit({ profileId: matchProfileId, text: detail.postingText }, { signedIn: loaded ? !!user : null });
    return undefined;
  };

  const check = useCallback(
    async (id: string) => {
      try {
        return (await getListing(id, profile.id)) ? ('open' as const) : ('gone' as const);
      } catch {
        return 'unknown' as const;
      }
    },
    [profile.id],
  );

  return (
    <div className="dc-view">
      <DiscoverHeader profileId={profile.id} tab={query.tab} onTab={(tab) => onQuery({ tab })} />
      <DiscoverScreen
        profileId={profile.id}
        query={query}
        state={state}
        cities={cities}
        localityDeclined={!!stored.localityDeclined}
        onQuery={onQuery}
        onArea={onArea}
        onDeclineLocality={onDeclineLocality}
        onPaste={() => actions.openMatch()}
        onAddProject={() => actions.goto('onboarding')}
        onSeeMatch={seeMatch}
        matchLocked={matchPhase === 'reading'}
        onOpenMatch={(id) => actions.openMatch({ screen: 'match', id })}
        check={check}
        banner={
          sample ? (
            <section className="dc-banner" aria-label="About this profile">
              <p className="dc-banner-text">
                These are roles for the sample profile, a frontend engineer in Bengaluru. Analyse your own resume to see roles that fit you.
              </p>
              <button type="button" className="btn dc-btn" onClick={() => actions.goto('onboarding')}>
                Analyse my resume
              </button>
            </section>
          ) : undefined
        }
      />
    </div>
  );
}
