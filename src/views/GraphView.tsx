/**
 * One next role, opened from the dashboard: the role as the hero (its name, its alignment, the radar of you against it with
 * the two widest gaps beside it) and a mosaic of materials beside it (NextGraph). The dashboard holds the skill radar itself,
 * so this screen has no Now | Next control; a quiet line at the top leads back.
 */
import type { Profile } from '../../shared/types';
import { actions, useApp } from '../store';
import { GraphHeader } from './graph/GraphHeader';
import { NextGraph } from './graph/NextGraph';
import '../styles/shell.css';

export function GraphView({ profile }: { profile: Profile }) {
  const roleId = useApp((s) => s.openRoleId);

  return (
    <div className="gv-view">
      <GraphHeader profile={profile} current="role" />

      <main className="gv-role-main">
        <nav className="gv-crumb" aria-label="Breadcrumb">
          <button type="button" className="btn btn-quiet btn-sm gv-crumb-back" onClick={() => actions.showDashboard()}>
            ← Dashboard
          </button>
          <span className="kicker">Next role</span>
        </nav>
        <section className="gv-face gv-role" data-show="true" aria-label="Next roles: role radar">
          <NextGraph profile={profile} initialRoleId={roleId} />
        </section>
      </main>
    </div>
  );
}
