/**
 * The header of the resume screens: the wordmark, one way back (the dashboard from the resumes, the resume from
 * Finalise and from the blocks), a link to the blocks with their count, and whatever the caller puts at the end (the
 * atmosphere and the account control in the app; nothing in the dev gallery).
 */
import type { ReactNode } from 'react';
import { Brand } from '../components/Brand';
import { ScreenLink } from './parts';
import type { ResumeQuery } from './route';

interface ResumeHeaderProps {
  route: ResumeQuery;
  /** How many blocks the person has, once known. */
  blocks?: number;
  onHome?: () => void;
  onNavigate: (to: ResumeQuery) => void;
  end?: ReactNode;
}

export function ResumeHeader({ route, blocks, onHome, onNavigate, end }: ResumeHeaderProps) {
  const toResume: ResumeQuery = { screen: 'resume', id: route.id };
  return (
    <header className="rs-header">
      <div className="rs-header-start">
        <Brand onClick={onHome} />
        {route.screen === 'resume' ? (
          onHome && (
            <button type="button" className="rs-navbtn" onClick={onHome}>
              ← Dashboard
            </button>
          )
        ) : (
          <ScreenLink to={toResume} onNavigate={onNavigate} className="rs-navbtn">
            {route.screen === 'finalise' ? '← Back to the resume' : '← Your resumes'}
          </ScreenLink>
        )}
      </div>
      <div className="rs-header-end">
        {route.screen !== 'blocks' && blocks !== undefined && (
          <ScreenLink to={{ screen: 'blocks', id: route.id }} onNavigate={onNavigate} className="rs-navbtn">
            Your blocks · <span className="numeral">{blocks}</span>
          </ScreenLink>
        )}
        {end}
      </div>
    </header>
  );
}
