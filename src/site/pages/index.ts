import type { ContentPage } from '../types';
import { about } from './About';
import { careerPlan } from './CareerPlan';
import { howItWorks } from './HowItWorks';
import { jobMatch } from './JobMatch';
import { jobSearch } from './JobSearch';
import { mockInterview } from './MockInterview';
import { resumeToPortfolio } from './ResumeToPortfolio';
import { skillGraph } from './SkillGraph';

/**
 * Every content page of the site. To add one: write a module here, add it to this list, and add its path to SITE_PAGES
 * in ../links.ts as a `page` (scripts/build-pages.tsx fails the build when the two lists disagree).
 */
export const CONTENT_PAGES: readonly ContentPage[] = [
  howItWorks,
  skillGraph,
  jobMatch,
  mockInterview,
  jobSearch,
  careerPlan,
  resumeToPortfolio,
  about,
];
