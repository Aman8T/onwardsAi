import type { ReactNode } from 'react';
import { Radar } from 'tayyari';
import type { RadarAxis } from 'tayyari';

/** Riya Kapoor's six areas (the sample persona), in the graph's fixed order, with what a Design Engineer role expects. */
const AREAS: RadarAxis[] = [
  { id: 'frontend', label: 'Frontend', value: 81, secondary: 85 },
  { id: 'design-motion', label: 'Design & Motion', value: 85, secondary: 70 },
  { id: 'backend-apis', label: 'Backend & APIs', value: 58, secondary: 45 },
  { id: 'ai-llm', label: 'AI & LLM', value: 36, secondary: 55 },
  { id: 'eng-practice', label: 'Eng Practice', value: 55, secondary: 65 },
  { id: 'product-collab', label: 'Product & Collab', value: 55, secondary: 60 },
];
const YOU = AREAS.map(({ secondary: _s, ...a }) => a);

const frame = (children: ReactNode) => <div style={{ maxWidth: 480, margin: '0 auto' }}>{children}</div>;

/** One series: the person's own shape. */
export const You = () =>
  frame(
    <Radar
      axes={YOU}
      axisSetKey="areas"
      series={{ primary: 'You' }}
      ariaLabel="Your skill areas"
      tableCaption="Skill areas, 0 to 100"
    />,
  );

/** Two series: you bold, the role light under a dashed hairline (benchmark thin, actual bold). */
export const AgainstRole = () =>
  frame(
    <Radar
      axes={AREAS}
      axisSetKey="areas"
      series={{ primary: 'You', secondary: 'Design Engineer expects' }}
      ariaLabel="Your skill areas against the Design Engineer role"
      tableCaption="Skill areas against the role, 0 to 100"
    />,
  );

/** A selected axis (it opens a popover, so `axisState="expanded"`), the related area kept bright, the rest dimmed. */
export const Selected = () =>
  frame(
    <Radar
      axes={YOU}
      axisSetKey="areas"
      series={{ primary: 'You' }}
      selectedId="design-motion"
      axisState="expanded"
      relatedIds={new Set(['frontend'])}
      dim
      ariaLabel="Your skill areas"
      tableCaption="Skill areas, 0 to 100"
    />,
  );

/** The centre slot carries the one number the chart summarises. */
export const WithCentre = () =>
  frame(
    <Radar
      axes={YOU}
      axisSetKey="areas"
      series={{ primary: 'You' }}
      ariaLabel="Your skill areas"
      tableCaption="Skill areas, 0 to 100"
      centre={
        <div style={{ textAlign: 'center' }}>
          <span className="numeral" style={{ display: 'block', fontSize: 'var(--fs-6)', lineHeight: 1 }}>
            78
          </span>
          <span className="kicker">Mastery</span>
        </div>
      }
    />,
  );
