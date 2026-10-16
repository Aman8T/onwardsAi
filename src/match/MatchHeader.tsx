/**
 * The header of the match screens: the wordmark, the sections of the app as quiet chips (Match is where you are),
 * and on the right who you are, what your next posting will use, the atmosphere and the account control.
 * Plain, flat and on the field's own tone, like the graph header, but not sticky: these are reading screens.
 */
import { AccountMenu } from '../auth/AccountMenu';
import { Atmosphere } from '../components/Atmosphere';
import { Brand } from '../components/Brand';
import { actions, useApp } from '../store';
import { EntitlementChip } from './EntitlementChip';
import { RouteLink } from './parts';
import { PASTE_ROUTE } from './route';
import type { MatchRoute } from './route';

export function MatchHeader({ onNavigate }: { onNavigate: (route: MatchRoute) => void }) {
  const profile = useApp((s) => s.profile);
  const who = profile ? [profile.person.name, profile.person.currentRole].filter(Boolean).join(' · ') : '';

  const home = () => (profile ? actions.showGraph() : actions.goto('onboarding'));
  const graph = (flipped: boolean) => {
    actions.setFlipped(flipped);
    actions.showGraph();
  };

  return (
    <header className="mt-header">
      <Brand onClick={home} />
      {profile && (
        <nav className="mt-nav" aria-label="Sections">
          <button type="button" className="mt-navbtn" onClick={() => graph(false)}>
            Now
          </button>
          <button type="button" className="mt-navbtn" onClick={() => graph(true)}>
            Next
          </button>
          <button type="button" className="mt-navbtn" onClick={() => void actions.openPortfolio()}>
            Portfolio
          </button>
          <RouteLink route={PASTE_ROUTE} onNavigate={onNavigate} className="mt-navbtn is-current" current>
            Match
          </RouteLink>
          <button type="button" className="mt-navbtn" onClick={() => actions.openDiscover()}>
            Discover
          </button>
        </nav>
      )}
      <div className="mt-header-end">
        {who && <span className="mt-who">{who}</span>}
        <EntitlementChip />
        <Atmosphere />
        <AccountMenu />
      </div>
    </header>
  );
}
