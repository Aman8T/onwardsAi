/**
 * The stats slot beside or under a graph: 2–4 real source facts ("389 GitHub stars") and, once the practice
 * feature exists and fills `content.practice`, a second group of practice counts. Values are shown exactly as
 * the content carries them.
 */
import type { PracticeStats, ProofStat } from '../../../shared/portfolio';

interface Props {
  stats: ProofStat[];
  practice?: PracticeStats;
  /** Show the source facts (false when the page's proof block already prints them). */
  showFacts: boolean;
  /** `row`: numerals over labels, wrapping; `list`: label left, numeral right (the atlas caption). */
  layout?: 'row' | 'list';
}

const MAX_FACTS = 4;

export function StatsStrip({ stats, practice, showFacts, layout = 'row' }: Props) {
  const facts = showFacts ? stats.slice(0, MAX_FACTS) : [];
  if (facts.length === 0 && !practice) return null;
  const practiceItems = practice
    ? [
        { n: practice.sessions, l: 'practice sessions' },
        { n: practice.mockInterviews, l: 'mock interviews' },
        { n: practice.domainsPractised, l: 'domains practised' },
      ]
    : [];
  return (
    <div className="pg-stats pg-in" data-late="" data-layout={layout}>
      {facts.length > 0 && (
        <ul className="pg-stat-list" aria-label="Source facts">
          {facts.map((s) => (
            <li key={`${s.sourceId}:${s.label}`} className="pg-stat">
              <span className="pg-stat-n">{s.value}</span>
              <span className="pg-stat-l">{s.label}</span>
            </li>
          ))}
        </ul>
      )}
      {practice && (
        <div className="pg-practice">
          <p className="pg-eyebrow">Practice</p>
          <ul className="pg-stat-list" aria-label="Practice">
            {practiceItems.map((s) => (
              <li key={s.l} className="pg-stat">
                <span className="pg-stat-n">{s.n}</span>
                <span className="pg-stat-l">{s.l}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
