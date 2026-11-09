/**
 * The /weekly screen: The Leafout Weekly's reading room for the signed-in reader. It loads the subscription and the
 * list of editions, opens the one in the address (`/weekly?e=<id>`, else the latest) and hands everything to
 * `WeeklyRoom`. Reading an edition marks it read on the server, so the unread count is asked for again afterwards.
 * Nobody signed in sees the sample edition and a way in.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { WeeklyEdition, WeeklyEditionSummary } from '../../shared/weekly';
import { AccountMenu } from '../auth/AccountMenu';
import { signInSheet, useMe } from '../auth/useMe';
import { Atmosphere } from '../components/Atmosphere';
import { Brand } from '../components/Brand';
import { actions, useApp } from '../store';
import { getEdition, listEditions, previewEdition } from './api';
import { setSubscription, useSubscription } from './useSubscription';
import { WeeklyRoom } from './WeeklyRoom';
import '../styles/weekly.css';

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function WeeklyView() {
  const route = useApp((s) => s.weeklyRoute);
  const { user, loaded, status } = useMe();
  const userId = user?.id;
  const subscription = useSubscription(userId);

  const [editions, setEditions] = useState<WeeklyEditionSummary[] | null>(null);
  const [listError, setListError] = useState('');
  const [edition, setEdition] = useState<WeeklyEdition | undefined>();
  const [editionLoading, setEditionLoading] = useState(false);
  const [editionError, setEditionError] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const shown = useRef<string | undefined>(undefined);

  const loadList = useCallback(async (signal?: AbortSignal) => {
    try {
      const list = await listEditions(signal);
      setEditions(list.editions);
      setListError('');
    } catch (err) {
      if (signal?.aborted) return;
      setListError(message(err));
      setEditions((cur) => cur ?? []);
    }
  }, []);

  // the list of editions, once per signed-in account
  useEffect(() => {
    setEditions(null);
    setEdition(undefined);
    shown.current = undefined;
    if (!userId) return;
    const ctrl = new AbortController();
    void loadList(ctrl.signal);
    return () => ctrl.abort();
  }, [userId, loadList]);

  // the edition on screen: the one in the address, else the latest
  const target = route.edition ?? editions?.[0]?.id;
  useEffect(() => {
    if (!userId || editions === null) return;
    if (!target) {
      setEdition(undefined);
      return;
    }
    if (shown.current === target) return;
    const ctrl = new AbortController();
    setEditionLoading(true);
    setEditionError('');
    getEdition(target, ctrl.signal)
      .then((next) => {
        shown.current = next.id;
        setEdition(next);
        setEditions((cur) => (cur ? cur.map((e) => (e.id === next.id ? { ...e, read: true } : e)) : cur));
        void subscription.reload();
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setEditionError(message(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setEditionLoading(false);
      });
    return () => ctrl.abort();
    // the subscription reload is a stable callback per account; the effect follows the target, not it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, editions === null, target]);

  async function preview() {
    setPreviewing(true);
    setPreviewError('');
    try {
      const next = await previewEdition();
      shown.current = next.id;
      setEdition(next);
      await loadList();
      actions.openWeekly({ edition: next.id }, { replace: true });
      void subscription.reload();
    } catch (err) {
      setPreviewError(message(err));
    } finally {
      setPreviewing(false);
    }
  }

  // a failed /api/me reads as anonymous, as everywhere else in the app
  const mode =
    !loaded && status !== 'error'
      ? 'loading'
      : !user
        ? 'signed-out'
        : subscription.status === 'error'
          ? 'error'
          : subscription.subscription && editions !== null
            ? 'ready'
            : 'loading';

  return (
    <div className="wk-page">
      <header className="wk-header">
        <Brand onClick={() => actions.showDashboard()} />
        <span className="kicker wk-header-kicker">The Leafout Weekly</span>
        <div className="wk-header-end">
          <Atmosphere />
          <AccountMenu />
        </div>
      </header>
      <main className="wk-main">
        <WeeklyRoom
          mode={mode}
          error={subscription.error}
          onRetry={() => void subscription.reload()}
          onSignIn={() => signInSheet.open('account', undefined, '/weekly')}
          subscription={subscription.subscription ?? undefined}
          onSaved={setSubscription}
          editions={editions ?? []}
          edition={edition}
          editionLoading={editionLoading}
          editionError={editionError || listError}
          onOpenEdition={(id) => actions.openWeekly({ edition: id })}
          onPreview={() => void preview()}
          previewing={previewing}
          previewError={previewError}
        />
      </main>
    </div>
  );
}
