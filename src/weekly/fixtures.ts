/**
 * Fixtures for the empty state and the dev gallery (`/dev/weekly`): one sample edition for Riya Kapoor, the sample
 * persona (a frontend engineer in Bengaluru), built only from the Discover dev fixtures (Headout, GitLab, SonicWall)
 * and the facts they cite. News items are placeholders, clearly marked, and no number appears that those fixtures do not
 * already state. The empty state of the screen shows this edition under "Sample edition".
 */
import type { WeeklyEdition, WeeklyEditionSummary, WeeklyFit, WeeklyJob } from '../../shared/weekly';
import type { AiJob } from '../../shared/jobsearch';
import { GITLAB, HEADOUT, SONICWALL_PDE } from '../discover/dev/fixtures';

const FIT: Record<AiJob['fit'], WeeklyFit> = {
  strong: 'looks_close',
  good: 'looks_close',
  stretch: 'stretch',
};

function job(j: AiJob, withMet: boolean, note?: string): WeeklyJob {
  return {
    listingId: j.id,
    employer: j.employer,
    title: j.title,
    place: j.place.label,
    applyUrl: j.applyUrl,
    fit: FIT[j.fit],
    met: withMet ? j.met.filter((m) => !m.partly).map((m) => ({ ask: m.ask, cite: m.cite })) : [],
    notShown: withMet ? j.gaps : [],
    note,
  };
}

const TIP = 'Put the number in the first five words. "Cut checkout LCP from 4.1s to 1.9s" beats "Worked on performance".';

export const SAMPLE_EDITION: WeeklyEdition = {
  id: 'sample-edition-4',
  issue: 4,
  weekOf: '2026-10-26',
  headline: 'Three new openings fit your graph this week',
  reader: 'Riya Kapoor, Bengaluru',
  place: 'Bengaluru',
  jobs: [
    job(
      HEADOUT,
      true,
      'Two of the three asks are already on your record. The third is nowhere in what you have shared: one line on your resume would cover it.',
    ),
    job(GITLAB, false, 'Asks for a step up in level. One ask is met on your record and one partly.'),
    job(
      SONICWALL_PDE,
      false,
      'Asks for a step up in level. Two asks are met in part; nothing you shared shows end-to-end ownership of a product area yet.',
    ),
  ],
  progress: {
    closestRole: 'Design Engineer',
    closestEta: '~2 months',
    nudge: 'This week: one ten-minute Defend your resume round.',
  },
  news: [
    {
      headline: '[A headline found this week on design systems]',
      publication: '[Publication]',
      url: '',
      why: '[Two sentences: what happened, and why it matters to someone with your graph, citing the article.]',
      skill: 'Design systems',
    },
    {
      headline: '[A headline on motion and animation on the web]',
      publication: '[Publication]',
      url: '',
      why: '[Two sentences, cited.]',
      skill: 'React',
    },
    {
      headline: '[A headline on frontend hiring in Bengaluru]',
      publication: '[Publication]',
      url: '',
      why: '[Two sentences, cited.]',
      skill: 'TypeScript',
    },
  ],
  tip: TIP,
  quote: "A gap isn't a no. It's a to-do.",
  createdAt: '2026-10-26T00:30:00.000Z',
  emailedAt: '2026-10-26T01:00:00.000Z',
};

/** The same edition with every part of "Your week" filled: layout test values, never shown to a reader. */
export const FULL_PROGRESS_EDITION: WeeklyEdition = {
  ...SAMPLE_EDITION,
  id: 'layout-test-full-progress',
  progress: {
    ...SAMPLE_EDITION.progress,
    planRole: 'Design Engineer',
    planWeek: 1,
    planWeeks: 2,
    ticksThisWeek: 1,
    ticksPlanned: 2,
    lastPractice: '2026-10-12',
  },
};

/** One real-looking article, to see a linked news item (the URL is the example domain). */
export const NEWS_LINKED_EDITION: WeeklyEdition = {
  ...SAMPLE_EDITION,
  id: 'layout-test-news',
  news: SAMPLE_EDITION.news.map((n, i) => ({
    ...n,
    url: 'https://example.com/article-' + (i + 1),
    date: '2026-10-2' + (i + 1),
  })),
};

/** Nothing new fit, no news, no progress: the quietest week. */
export const QUIET_EDITION: WeeklyEdition = {
  ...SAMPLE_EDITION,
  id: 'layout-test-quiet',
  issue: 5,
  weekOf: '2026-11-02',
  headline: 'A quiet week on the boards',
  jobs: [],
  progress: undefined,
  news: [],
};

/** A week with no tip or quote section turned on and only the lead job. */
export const ONE_JOB_EDITION: WeeklyEdition = {
  ...SAMPLE_EDITION,
  id: 'layout-test-one-job',
  headline: 'One new opening fits your graph this week',
  jobs: SAMPLE_EDITION.jobs.slice(0, 1),
  tip: undefined,
  quote: undefined,
};

export const SAMPLE_ARCHIVE: WeeklyEditionSummary[] = [
  {
    id: 'sample-edition-4',
    issue: 4,
    weekOf: '2026-10-26',
    headline: SAMPLE_EDITION.headline,
    read: false,
  },
  {
    id: 'sample-edition-3',
    issue: 3,
    weekOf: '2026-10-19',
    headline: "[that week's lead headline]",
    read: true,
  },
  {
    id: 'sample-edition-2',
    issue: 2,
    weekOf: '2026-10-12',
    headline: "[that week's lead headline]",
    read: true,
  },
  {
    id: 'sample-edition-1',
    issue: 1,
    weekOf: '2026-10-05',
    headline: "[that week's lead headline]",
    read: true,
  },
];
