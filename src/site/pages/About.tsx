import type { ContentPage } from '../types';

export const about: ContentPage = {
  path: '/about',
  title: 'About Leafout: who it is for and what it refuses',
  description:
    'Leafout helps students in India and working professionals of every profession prove what they have done. It refuses invented numbers, fake scores and scraping.',
  h1: 'Leafout doesn’t write claims. It proves them.',
  lede: 'Leafout is what a plant does when it is ready to grow. It helps people show what they have actually done, see what a role expects that they cannot yet show, and close the gap with work rather than words.',
  blurb: 'Who Leafout is for, what it does, and what it refuses to do.',
  cta: { label: 'Start with a resume', href: '/' },
  related: ['/how-it-works', '/job-match', '/skill-graph'],
  faq: [
    {
      q: 'Who is Leafout for?',
      a: 'Students in India, directly and not through their colleges, and working professionals of every profession, in India and elsewhere. A nurse, an accountant, a marketer and an engineer get the same product.',
    },
    {
      q: 'Does Leafout write my resume for me?',
      a: 'It builds a resume for a specific posting, but only from facts you can cite. It does not add skills the posting wants and your evidence does not show, and it asks you for any number it is missing instead of inventing one.',
    },
    {
      q: 'How is this different from a keyword optimiser?',
      a: 'Leafout does not stuff keywords. A skill reaches a resume only when your evidence supports it, and a gap is written as a gap or left out.',
    },
    {
      q: 'Does Leafout scrape LinkedIn?',
      a: 'No. It does not scrape LinkedIn, Naukri or any site that forbids automated access. You can paste text from them, and it can read public pages through guarded requests.',
    },
    {
      q: 'Is Leafout finished?',
      a: 'No. It is an early product. Some parts, such as interview practice, are free while in testing. A portfolio on your own domain is planned and not built yet.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>Why it exists</h2>
        <p>
          AI-written resumes are everywhere now, so a resume alone tells an employer very little, and employers answer with automated screens and
          silence. The people hurt most are the ones with the least signal to begin with: students, people changing field, and anyone who did not work
          at a famous company.
        </p>
        <p>
          Leafout takes the other side of that trade. It does not help you claim more. It helps you prove what you already have, and it shows you
          exactly what a role expects that you cannot yet show.
        </p>
      </section>

      <section>
        <h2>Who it is for</h2>
        <ul>
          <li>
            <strong>Students in India.</strong> Most are unplaced at graduation and are told to pad their resumes and stuff keywords. Leafout is
            built for them directly, and nobody should have to buy a year of anything to find out whether it helps.
          </li>
          <li>
            <strong>Working professionals of every profession.</strong> The resume is the universal evidence. GitHub, deployed apps and public links
            strengthen it when they exist.
          </li>
        </ul>
      </section>

      <section>
        <h2>What it does</h2>
        <ul>
          <li>
            Reads what you have done and builds a <a href="/skill-graph">skill graph</a> where every skill cites its source.
          </li>
          <li>
            Checks a <a href="/job-match">job posting</a> against your evidence and builds a resume and cover note from facts you can defend.
          </li>
          <li>
            Finds <a href="/job-search">roles</a> on employers&apos; own boards and explains each fit in words.
          </li>
          <li>
            Turns the gaps into a <a href="/career-plan">plan</a>, and lets you <a href="/mock-interview">practise the interview</a> on your own work.
          </li>
          <li>
            Makes a <a href="/resume-to-portfolio">portfolio page</a> from the same evidence, and a radar card of your skills that you can place in a GitHub or LinkedIn profile.
          </li>
        </ul>
      </section>

      <section>
        <h2>What it refuses to do</h2>
        <div className="panel callout">
          <ul>
            <li>
              <strong>No claim without a source.</strong> A sentence about you exists only if it cites a fact with a line, a link or your own words.
            </li>
            <li>
              <strong>No invented numbers.</strong> A bullet that would be stronger with a number says so and asks you.
            </li>
            <li>
              <strong>No skill from a posting onto your resume.</strong> What you cannot evidence is a gap.
            </li>
            <li>
              <strong>No fake scores.</strong> No percentage ATS match and no pass/fail per vendor. Bands, not decimals; checklists, not grades.
            </li>
            <li>
              <strong>No scraping</strong> of people&apos;s accounts or of job boards that forbid it.
            </li>
            <li>
              <strong>No personality screening.</strong> Culture fit and coachability cannot be read from documents.
            </li>
            <li>
              <strong>No annual lock-in and no dark patterns.</strong> You pay per posting or by the month. Seeing a gap is free, and so is answering a
              question about yourself.
            </li>
          </ul>
        </div>
      </section>

      <section>
        <h2>How it keeps those promises</h2>
        <p>
          Code computes the scores, weights and bands. Models read, classify and phrase, always into typed slots that code checks. A model has to cite
          the facts behind a verdict before it may state the verdict, and code downgrades any verdict that has no valid citation. Each of these rules
          has an automated check.
        </p>
      </section>

      <section>
        <h2>Where it stands</h2>
        <p>
          Leafout is early. The <a href="/how-it-works">pipeline</a> works end to end, and the parts that are still in testing are marked as such.
        </p>
        <p>
          The company site is <a href="https://leafout.in">leafout.in</a>. It has the <a href="https://leafout.in/blog">blog</a>, the{' '}
          <a href="https://leafout.in/privacy">privacy policy</a>, the <a href="https://leafout.in/terms">terms</a> and the{' '}
          <a href="https://leafout.in/refunds">refund policy</a>.
        </p>
      </section>
    </>
  ),
};
