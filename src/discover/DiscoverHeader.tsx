/**
 * The header of the Discover screens: the wordmark, the sections of the app as quiet chips (Discover is where you
 * are), and on the right who you are, the person's open applications, the atmosphere and the account control. Plain,
 * flat and on the field's own tone, like the match header. With auto-apply on and someone signed in, "Applications"
 * links to the applications sent through Leafout (`/apply`); "Your applications" stays the roles saved in this browser.
 */
import { applyPath } from '../apply/route';
import { applyOn, useApplyStatus } from '../apply/useApplications';
import { AccountMenu } from '../auth/AccountMenu';
import { useMe } from '../auth/useMe';
import { Atmosphere } from '../components/Atmosphere';
import { Brand } from '../components/Brand';
import { actions, useApp } from '../store';
import { useTracker } from './applications/tracker';
import type { DiscoverTab } from './route';
import '../styles/discover.css';

export interface DiscoverHeaderProps {
  profileId: string;
  tab: DiscoverTab;
  onTab: (tab: 'roles' | 'applications') => void;
}

export function DiscoverHeader({ profileId, tab, onTab }: DiscoverHeaderProps) {
  const profile = useApp((s) => s.profile);
  const who = profile ? [profile.person.name, profile.person.currentRole].filter(Boolean).join(' · ') : '';
  // the roles still moving: a closed one is history, not something waiting on the person
  const open = useTracker(profileId).items.filter((t) => t.stage !== 'closed').length;
  const apps = tab === 'applications';
  const { user } = useMe();
  const sent = applyOn(useApplyStatus()) && !!user;

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
        <button type="button" className={`dc-navbtn${apps ? '' : ' is-current'}`} aria-current={apps ? undefined : 'page'} onClick={() => onTab('roles')}>
          Discover
        </button>
        <button type="button" className="dc-navbtn" onClick={() => actions.openPractice({ screen: 'hub' })}>
          Practice
        </button>
      </nav>
      <div className="dc-header-end">
        {who && <span className="dc-who">{who}</span>}
        {sent && (
          <a className="dc-navbtn" href={applyPath({ screen: 'list' })}>
            Applications
          </a>
        )}
        <button type="button" className={`dc-navbtn${apps ? ' is-current' : ''}`} aria-current={apps ? 'page' : undefined} onClick={() => onTab('applications')}>
          Your applications{open > 0 ? ` · ${open}` : ''}
        </button>
        <Atmosphere />
        <AccountMenu />
      </div>
    </header>
  );
}
