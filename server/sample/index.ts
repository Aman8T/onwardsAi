/**
 * The bundled sample persona behind "Try a sample profile": Riya Kapoor, a fictional frontend engineer
 * (the same person as the design canvas). The fixtures are fed to the REAL Haiku readers and Sonnet
 * synthesizer; only the deterministic fetching step is replaced by replayed logs.
 *
 * Everything here is fictional: Kitebird and Saffron Ledger are made-up companies.
 */
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Profile } from '../../shared/types.ts';
import type { RawSource } from '../ingest/types.ts';

const DIR = dirname(fileURLToPath(import.meta.url));

export const SAMPLE_GOAL = 'Move into AI product engineering within a year';

/** Pre-computed profile for instant, offline demos (written by `npm run make-sample`). */
export const SAMPLE_PROFILE_PATH = join(DIR, 'sample-profile.json');
/** Chat context (source text + reader outputs) for the pre-computed profile. */
export const SAMPLE_CONTEXT_PATH = join(DIR, 'sample-context.json');

/** Fake ingest log lines replayed (with short delays) in sample mode, per source id. */
export const SAMPLE_INGEST_LOGS: Record<string, string[]> = {
  resume: ['Parsed 2 pages', 'Read 656 words of text', 'Found sections: Summary, Experience, Projects, Skills, Education'],
  github: [
    'Fetched profile, 34 public repos',
    'Ranked repos by recency and stars',
    'motion-kit: spring physics, 212 stars',
    'Detected tests in 6 of 34 repos, CI in 9',
    'notes-ai: embeddings + OpenAI API',
  ],
  'lovable-1': ['Opened recipe-genie.lovable.app', 'Detected Supabase auth, OpenAI, Tailwind, shadcn/ui', 'Mapped 3 user flows'],
  'portfolio-1': ['riyakapoor.design → 8 pages', 'Found 5 case studies', 'Found accessibility audit write-up'],
};

function read(name: string): string {
  return readFileSync(join(DIR, name), 'utf8').replace(/\r\n?/g, '\n').trim();
}

export function loadSampleSources(): RawSource[] {
  return [
    {
      id: 'resume',
      kind: 'resume',
      label: 'Resume',
      src: 'Riya_Kapoor_Resume.pdf',
      kindLabel: 'Resume · PDF',
      content: read('resume.md'),
      facts: {
        pages: 2,
        words: 656,
        format: 'PDF',
        sections: ['Summary', 'Experience', 'Projects', 'Skills', 'Education', 'Achievements'],
        roles: ['Kitebird', 'Saffron Ledger', 'Freelance', 'GDSC'],
      },
      quickSignals: ['React', 'TypeScript', 'Figma', 'Mentored 2 interns', 'Node.js', 'GraphQL', 'Storybook', 'Accessibility'],
    },
    {
      id: 'github',
      kind: 'github',
      label: 'GitHub',
      url: 'https://github.com/riyakapoor',
      src: 'github.com/riyakapoor',
      kindLabel: 'GitHub profile',
      content: read('github.md'),
      facts: {
        username: 'riyakapoor',
        repos: 34,
        originals: 31,
        forks: 3,
        stars: 389,
        languages: ['TypeScript', 'JavaScript', 'CSS', 'MDX', 'Python', 'Go', 'Shell', 'Lua', 'HTML'],
        topRepos: ['motion-kit', 'design-tokens', 'notes-ai', 'checkout-lab', 'recipe-genie-web', 'a11y-checklist', 'trpc-starter'],
        withTests: 6,
        withCI: 9,
        inspected: 34,
        followers: 318,
        since: '2019-08-14T09:12:44Z',
      },
      quickSignals: ['TypeScript · 17 repos', 'JavaScript · 6 repos', 'React · 14 repos', 'Framer Motion · motion-kit', 'OpenAI API · notes-ai', 'CI in 9 repos', 'Tests in 6 repos', '389 stars'],
    },
    {
      id: 'lovable-1',
      kind: 'lovable',
      label: 'Lovable',
      url: 'https://recipe-genie.lovable.app',
      src: 'recipe-genie.lovable.app',
      kindLabel: 'Lovable app',
      content: read('lovable.md'),
      facts: {
        title: 'Recipe Genie',
        headings: ['Pantry in seconds', 'Three good options', 'Swap anything', 'Save your favourites'],
        libraries: ['React', 'Vite', 'Tailwind CSS', 'shadcn/ui', 'Supabase', 'OpenAI API'],
        flows: 3,
      },
      quickSignals: ['Rapid prototyping', 'Supabase', 'OpenAI API', 'Tailwind CSS', 'shadcn/ui'],
    },
    {
      id: 'portfolio-1',
      kind: 'portfolio',
      label: 'Portfolio',
      url: 'https://riyakapoor.design',
      src: 'riyakapoor.design',
      kindLabel: 'Portfolio site',
      content: read('portfolio.md'),
      facts: {
        pages: 8,
        caseStudies: 5,
        headings: ['Checkout, rebuilt', 'An accessibility audit that actually shipped', 'Kite UI', 'motion-kit: designing with springs', 'Saffron Ledger dashboard'],
      },
      quickSignals: ['Design systems', 'Accessibility audits', 'Motion design', 'Tech writing'],
    },
  ];
}

export function hasSampleProfile(): boolean {
  return existsSync(SAMPLE_PROFILE_PATH);
}

/** The pre-computed sample profile, or null when `npm run make-sample` has not been run. */
export function loadSampleProfile(): Profile | null {
  try {
    return JSON.parse(readFileSync(SAMPLE_PROFILE_PATH, 'utf8')) as Profile;
  } catch {
    return null;
  }
}
