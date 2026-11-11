/**
 * Whether the person's card is shared right now, and the public link if it is: one light GET of the same `/api/card`
 * the share sheet reads (`useCard`), asked once per graph. It fails softly: on any error, on a graph that cannot share
 * (`blocked`), or where there is no card API (the dev galleries), the answer is `null` and the caller looks as it did
 * before. It never writes, and it never shows an error.
 */
import { useEffect, useState } from 'react';
import { isAbort, realClient } from './api';
import type { CardClient } from './api';

/** A link only if it is an address of ours or a web address: never `javascript:` or the like (as in the share sheet). */
const safeHref = (u: string | undefined): string | undefined => (u && /^(https?:\/\/|\/)/i.test(u) ? u : undefined);

/** The public page's address while sharing is on; `null` when it is off, not known, or could not be read. */
export function useCardLink(profileId: string, client: CardClient = realClient): string | null {
  const [link, setLink] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    setLink(null);
    client
      .get(profileId, ctrl.signal)
      .then((view) => {
        if (ctrl.signal.aborted) return;
        const href = view.blocked || !view.settings.shared || !view.slug ? undefined : safeHref(view.pageUrl);
        setLink(href ?? null);
      })
      .catch((err: unknown) => {
        if (!isAbort(err)) setLink(null);
      });
    return () => ctrl.abort();
  }, [profileId, client]);
  return link;
}
