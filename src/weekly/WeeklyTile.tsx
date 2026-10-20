/**
 * The Leafout Weekly's tile on the Home dashboard: the nameplate, one line, and a way in. "Subscribe, free" until the
 * reader is subscribed, then "Read this week's" with a chip for editions not yet opened.
 */
import { useEffect } from 'react';
import { useMe } from '../auth/useMe';
import { ensurePressFonts } from '../portfolio/press/fonts';
import { actions } from '../store';
import { useSubscription } from './useSubscription';
import '../styles/weekly.css';

export function WeeklyTile() {
  const { user } = useMe();
  const { subscription } = useSubscription(user?.id);
  useEffect(() => ensurePressFonts(), []);
  const subscribed = !!subscription && subscription.status !== 'off';
  const unread = subscribed ? subscription.unread : 0;

  return (
    <section className="panel ob-tile wk-tile" aria-labelledby="wk-tile-name">
      <h2 className="wk-tile-name" id="wk-tile-name">
        The Leafout Weekly
      </h2>
      <span className="ob-tile-line">Openings that fit you, your week and news from your field. Every Monday.</span>
      <div className="wk-tile-row">
        <button type="button" className={`btn ${subscribed ? '' : 'btn-primary'}`} onClick={() => actions.openWeekly()}>
          {subscribed ? "Read this week's" : 'Subscribe, free'}
        </button>
        {unread > 0 && <span className="chip wk-new">{unread} new</span>}
      </div>
    </section>
  );
}
