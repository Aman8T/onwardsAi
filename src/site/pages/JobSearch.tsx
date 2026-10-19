import type { ContentPage } from '../types';

export const jobSearch: ContentPage = {
  path: '/job-search',
  title: 'Job search from employers’ own boards',
  description:
    'Search with AI finds roles on employers’ own Greenhouse and Lever boards and says how you fit in words, not percentages. It never scrapes LinkedIn or Naukri.',
  h1: 'Search with AI: roles from employers’ own boards',
  lede: 'Tayyari’s job search starts from your skill graph and looks at roles that employers publish on their own hiring boards. Each role is explained in words, with quotes from the posting, and the apply link goes to the employer.',
  blurb: 'Roles from employers’ own boards, with the fit explained in words.',
  cta: { label: 'Search for roles', href: '/discover' },
  related: ['/job-match', '/career-plan', '/skill-graph'],
  faq: [
    {
      q: 'Does it search LinkedIn, Naukri or Indeed?',
      a: 'No. Those sites do not allow automated reading, so Tayyari does not scrape them. If you find a posting there, paste its text into job match instead.',
    },
    {
      q: 'How is the fit shown?',
      a: 'In words, with counts of what the role asks that you show, partly show or do not show. There is no percentage and no score.',
    },
    {
      q: 'Does Tayyari apply for me?',
      a: 'No. Each role links to the posting on the employer’s own page or hiring form, and you apply there.',
    },
    {
      q: 'Why Greenhouse and Lever?',
      a: 'Employers publish those boards openly for candidates. Other sources are added only where their terms allow it.',
    },
    {
      q: 'Does a search use my posting credits?',
      a: 'No, a search is not charged. Opening a full match for a role uses the same credits as any pasted posting.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>Where the roles come from</h2>
        <p>
          A search has two halves that run at once. One reads boards from Tayyari&apos;s own list of employers. The other sends scouts to search the
          web, limited to boards on Greenhouse and Lever, for the city you chose. The first cards appear within a few seconds, while the web half is
          still working.
        </p>
        <p>
          Tayyari does not scrape LinkedIn, Naukri or any site that forbids automated access. A scout never sees your name or your resume text.
        </p>
      </section>

      <section>
        <h2>How a search runs</h2>
        <p>
          The search brief is written by code from your skill graph: your current role, the roles you are heading for, your years of experience, your
          strongest skills and a few lines of your own evidence. You choose a city and whether to include remote roles, then press the button. The
          search runs only when you press it, and the last results are kept and shown when you come back.
        </p>
      </section>

      <section>
        <h2>Fit in words, not percentages</h2>
        <p>
          Each posting is read against your evidence. Every quote the read gives must appear in the posting word for word, and code checks it. The
          years a role asks for are read by code. The card shows how many of the role&apos;s asks you meet, partly meet or do not show, as counts.
        </p>
        <p>
          A posting with text aimed at an AI is flagged, and its prep page is built without a model. Tayyari does not invent a score for any role.
        </p>
      </section>

      <section>
        <h2>After you find a role</h2>
        <ul>
          <li>
            <strong>See how you fit</strong> sends the posting to <a href="/job-match">job match</a>.
          </li>
          <li>
            <strong>Prepare</strong> gives each gap the nearest evidence you have and one small step, plus three or four likely questions, each tied
            to a quote from the posting.
          </li>
          <li>
            <strong>Ask for a referral</strong> writes one short paragraph in three sentences: who you are, why you fit them, and why them. Every
            quote is checked against the posting and every name and number against your evidence.
          </li>
          <li>
            <strong>Compare</strong> sets roles you are weighing next to each other, and <strong>Not for me</strong> hides a role or an employer,
            and keeps out roles a step above your level if you ask.
          </li>
          <li>
            <strong>Your applications</strong> keeps track of what you have saved and applied to, in your browser.
          </li>
        </ul>
        <p>
          You can also <a href="/mock-interview">practise an interview</a> for a role from the results.
        </p>
      </section>

      <section>
        <h2>What it will not do</h2>
        <p>
          It will not apply for you, guess a salary, or promise that a role will answer. It shows you roles you may not have found yourself and tells
          you honestly how close each one is, so your time goes to the applications that are worth it. Saved results are removed after 30 days.
        </p>
      </section>
    </>
  ),
};
