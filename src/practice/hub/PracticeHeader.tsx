/**
 * The header of the Practice screens (hub, plan, debrief, progress): the wordmark, the sections of the app as quiet chips
 * (Practice is where you are), and on the right who you are, the person's open applications, the atmosphere and the
 * account control. Plain, flat and on the field's own tone, like the Discover and match headers. The room has no header:
 * it is one thin bar of its own, so nothing pulls the person out of the interview.
 */
import { AccountMenu } from '../../auth/AccountMenu';
import { useTracker } from '../../discover/applications/tracker';
import { Atmosphere } from '../../components/Atmosphere';
import { Brand } from '../../components/Brand';
import { actions, useApp } from '../../store';
import '../../styles/practice.css';

export interface PracticeHeaderProps {
  profileId: string;
  /** Who is practising ("Riya Kapoor · Frontend engineer"); absent = from the profile on screen. */
  who?: string;
  /** The dev page: every button says where it would go instead of going there. */
  onNavigate?: (where: string) => void;
}

export function PracticeHeader({ profileId, who, onNavigate }: PracticeHeaderProps) {
  const profile = useApp((s) => s.profile);
  const name = who ?? (profile ? [profile.person.name, profile.person.currentRole].filter(Boolean).join(' · ') : '');
  // the roles still moving: a closed one is history, not something waiting on the person
  const open = useTracker(profileId).items.filter((t) => t.stage !== 'closed').length;

  /** A button's action, or, on the dev page, a note of where it would go. */
  const go = (where: string, act: () => void) => () => (onNavigate ? onNavigate(`Gallery: opens ${where}.`) : act());

  return (
    <header className="pc-header">
      <Brand onClick={go('the dashboard', () => actions.showDashboard())} />
      <nav className="pc-nav" aria-label="Sections">
        <button type="button" className="pc-navbtn" onClick={go('the dashboard', () => actions.showDashboard())}>
          Dashboard
        </button>
        <button type="button" className="pc-navbtn" onClick={go('your portfolio', () => void actions.openPortfolio())}>
          Portfolio
        </button>
        <button type="button" className="pc-navbtn" onClick={go('Match', () => actions.openMatch())}>
          Match
        </button>
        <button type="button" className="pc-navbtn" onClick={go('Discover', () => actions.openDiscover())}>
          Discover
        </button>
        <button type="button" className="pc-navbtn is-current" aria-current="page" onClick={go('the Practice hub', () => actions.openPractice({ screen: 'hub' }))}>
          Practice
        </button>
      </nav>
      <div className="pc-header-end">
        {name && <span className="pc-who">{name}</span>}
        <button type="button" className="pc-navbtn" onClick={go('Your applications', () => actions.openDiscover({ tab: 'applications' }))}>
          Your applications{open > 0 ? ` · ${open}` : ''}
        </button>
        <Atmosphere />
        <AccountMenu />
      </div>
    </header>
  );
}
