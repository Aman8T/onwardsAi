/**
 * The header of the Discover screens: the wordmark, the sections of the app as quiet chips (Discover is where you
 * are), and on the right who you are, the person's saved roles, the atmosphere and the account control. Plain, flat
 * and on the field's own tone, like the match header.
 */
import { AccountMenu } from '../auth/AccountMenu';
import { Atmosphere } from '../components/Atmosphere';
import { Brand } from '../components/Brand';
import { actions, useApp } from '../store';
import { useTracker } from './tracker';
import '../styles/discover.css';

export function DiscoverHeader({ profileId, tab, onTab }: { profileId: string; tab: 'roles' | 'applications'; onTab: (tab: 'roles' | 'applications') => void }) {
  const profile = useApp((s) => s.profile);
  const who = profile ? [profile.person.name, profile.person.currentRole].filter(Boolean).join(' · ') : '';
  const saved = useTracker(profileId).items.length;

  const graph = (flipped: boolean) => {
    actions.setFlipped(flipped);
    actions.showGraph();
  };

  return (
    <header className="dc-header">
      <Brand onClick={() => actions.showGraph()} />
      <nav className="dc-nav" aria-label="Sections">
        <button type="button" className="dc-navbtn" onClick={() => graph(false)}>
          Now
        </button>
        <button type="button" className="dc-navbtn" onClick={() => graph(true)}>
          Next
        </button>
        <button type="button" className="dc-navbtn" onClick={() => void actions.openPortfolio()}>
          Portfolio
        </button>
        <button type="button" className="dc-navbtn" onClick={() => actions.openMatch()}>
          Match
        </button>
        <button type="button" className={`dc-navbtn${tab === 'roles' ? ' is-current' : ''}`} aria-current={tab === 'roles' ? 'page' : undefined} onClick={() => onTab('roles')}>
          Discover
        </button>
      </nav>
      <div className="dc-header-end">
        {who && <span className="dc-who">{who}</span>}
        <button type="button" className={`dc-navbtn${tab === 'applications' ? ' is-current' : ''}`} aria-current={tab === 'applications' ? 'page' : undefined} onClick={() => onTab('applications')}>
          Your applications{saved > 0 ? ` · ${saved}` : ''}
        </button>
        <Atmosphere />
        <AccountMenu />
      </div>
    </header>
  );
}
