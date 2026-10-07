import type { ReactNode } from 'react';
import type { ChatOrigin, Profile } from '../../../shared/types';
import { Ring } from '../../components/Ring';
import { IconCheck } from '../../components/Icons';
import { ORIGIN_WHERE } from './helpers';

const num = (s: string, re: RegExp): number | undefined => {
  const m = s.match(re);
  return m ? Number(m[1]) : undefined;
};

/**
 * The small visual that stands in for the node: a hairline ring with the alignment for roles,
 * a plain numeral for skills, domains and requirements. Sources, "you" and open questions have
 * none: the title and the caption carry them.
 */
function visualFor(origin: ChatOrigin, profile: Profile): ReactNode {
  switch (origin.kind) {
    case 'role': {
      const role = profile.roles.find((r) => r.id === origin.refId);
      const a = role?.alignment ?? num(origin.caption, /(\d+)\s*%/);
      if (a === undefined) break;
      return (
        <>
          <Ring value={a} size={36} stroke={1.25} />
          <span className="numeral ch-origin-pct">{Math.round(a)}%</span>
        </>
      );
    }
    case 'skill': {
      const score = profile.skills.find((s) => s.id === origin.refId)?.score ?? num(origin.caption, /(\d+)\s*\/\s*100/) ?? 50;
      return <span className="numeral ch-origin-score">{Math.round(score)}</span>;
    }
    case 'domain': {
      const score = profile.domains.find((d) => d.id === origin.refId)?.score ?? num(origin.caption, /(\d+)\s*\/\s*100/) ?? 50;
      return <span className="numeral ch-origin-score">{Math.round(score)}</span>;
    }
    case 'requirement': {
      const skill = profile.skills.find((s) => s.id === origin.refId);
      const isGap = /gap/i.test(origin.caption);
      // gap captions read "Role · gap 34→70": the first number is where the person is today.
      const score = skill?.score ?? (isGap ? num(origin.caption, /gap\s*(\d+)/i) : num(origin.caption, /(\d+)\s*\/\s*100/));
      if (score !== undefined) return <span className="numeral ch-origin-score">{Math.round(score)}</span>;
      return isGap ? <span className="kicker ch-origin-tag">Gap</span> : <IconCheck size={18} strokeWidth={1.5} />;
    }
    default:
      break;
  }
  return null;
}

/** "Started from" card. Click returns to the node on the graph. */
export function OriginCard({ origin, profile, onOpen }: { origin: ChatOrigin; profile: Profile; onOpen: () => void }) {
  const sub = origin.kind === 'free' ? origin.caption : `${ORIGIN_WHERE[origin.kind]} · ${origin.caption}`;
  const visual = visualFor(origin, profile);
  return (
    <button type="button" className={`ch-origin${visual ? '' : ' ch-origin--plain'}`} onClick={onOpen}>
      {visual && (
        <span className="ch-origin-vis" aria-hidden="true">
          {visual}
        </span>
      )}
      <span className="ch-origin-text">
        <span className="ch-origin-title">{origin.title}</span>
        <span className="kicker ch-origin-sub">{sub}</span>
        <span className="sr-only">Open on the graph</span>
      </span>
    </button>
  );
}
