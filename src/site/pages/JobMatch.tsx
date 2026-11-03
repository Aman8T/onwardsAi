import type { ContentPage } from '../types';

export const jobMatch: ContentPage = {
  path: '/job-match',
  title: 'Job posting match against your own evidence',
  description:
    'Paste a job posting and see every requirement checked against your evidence. Get a resume and cover note built only from facts you can cite. No ATS score.',
  h1: 'Match a job posting against what you can prove',
  lede: 'Paste a job posting. Leafout lists what it asks for, checks each requirement against your evidence, and tells you plainly where you are covered, partly covered or have a gap. Then it can build a resume and a cover note from facts you can defend.',
  blurb: 'Every requirement in a posting checked against your evidence.',
  cta: { label: 'Match a posting', href: '/match' },
  related: ['/skill-graph', '/career-plan', '/mock-interview'],
  faq: [
    {
      q: 'Does Leafout give an ATS score?',
      a: 'No. Nobody outside the vendors knows how a given applicant tracking system scores a resume, so a percentage or a pass/fail per vendor would be invented. Leafout shows a band in words and checks the things that can be checked.',
    },
    {
      q: 'Can I paste a link to a LinkedIn or Naukri posting?',
      a: 'No. LinkedIn, Naukri, Indeed, Glassdoor, foundit and Shine do not allow automated reading, so Leafout refuses their links and asks you to paste the posting text. A link to an employer’s own public page can be read.',
    },
    {
      q: 'Will it add skills from the posting to my resume?',
      a: 'Never. A requirement you cannot evidence stays a gap, and it is written as a gap or left out. A skill appears on the resume only when one of your facts supports it.',
    },
    {
      q: 'What if the posting contains instructions for an AI?',
      a: 'The posting is treated as data. An instruction aimed at a model is reported as an anomaly and is not followed, and links inside a posting are never opened.',
    },
    {
      q: 'What does it cost?',
      a: 'The first posting match each calendar month is free. After that you pay per posting or by the month, and there is no annual plan. The pricing page has the current plans.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>What you get</h2>
        <ul>
          <li>The posting broken into its requirements, each with the words of the posting it came from.</li>
          <li>Each requirement checked against your facts, with the evidence shown next to it.</li>
          <li>A band in words and a statement of how confident Leafout is, never a percentage.</li>
          <li>At most three questions about the gaps, must-haves first.</li>
          <li>A resume and a cover note for that posting, built from your cited facts.</li>
        </ul>
      </section>

      <section>
        <h2>The posting is read on its own first</h2>
        <p>
          Leafout reads the posting with your profile out of sight, so your resume cannot colour what the posting asked for. Every requirement is
          kept with a quote from the posting and a strength: must, required, preferred or nice. A requirement Leafout had to infer rather than find
          can never be marked as a must or required.
        </p>
        <p>
          Hard requirements, such as a licence, a degree or the right to work in a country, are checked before anything is scored. Only a clear
          failure on a serious one blocks a match. If your evidence is silent, Leafout says &quot;not evidenced, add it if true&quot;, not &quot;no&quot;.
        </p>
      </section>

      <section>
        <h2>Every requirement against your evidence</h2>
        <p>Each requirement gets one of six statuses:</p>
        <ul>
          <li>
            <strong>Covered:</strong> direct, specific evidence.
          </li>
          <li>
            <strong>Partial:</strong> close, with a gap.
          </li>
          <li>
            <strong>Synonym only:</strong> you show it, in other words.
          </li>
          <li>
            <strong>You have it, but your resume does not say so:</strong> it shows up elsewhere, for example in a repository.
          </li>
          <li>
            <strong>Gap:</strong> nothing in your evidence.
          </li>
          <li>
            <strong>Can&apos;t tell:</strong> not enough information. This is never counted as a fail.
          </li>
        </ul>
        <p>
          The model must cite the facts before it may state a status. If a status has no valid citation, code downgrades it to &quot;can&apos;t
          tell&quot;. The overall band, strong, good, stretch or weak, is worked out by code from the strength of each requirement and how well it is
          covered. A model does not choose it.
        </p>
        <p>
          The radar beside the table has one axis for each area of your own skill graph. A requirement that fits none of your areas is named under the
          chart. It is not hidden in an &quot;Other&quot; axis that would bend the shape towards nothing.
        </p>
      </section>

      <section>
        <h2>Questions turn gaps into facts</h2>
        <p>
          For a gap or an unknown, Leafout asks you a short question. Your answer becomes a fact in your own words, marked &quot;from you&quot;, and
          later matches can cite it. Re-checking after you answer is free.
        </p>
      </section>

      <section>
        <h2>A resume and cover note from cited facts</h2>
        <p>
          Code chooses which facts to use. A model phrases each line, and every sentence is verified against the facts it cites. If a line uses a
          stronger verb than the fact supports, such as &quot;led&quot; where the fact says &quot;contributed&quot;, it is flagged for you to decide.
          Numbers are never invented. A missing number becomes a question and a placeholder, [x], and the export stays blocked until you resolve it.
        </p>
        <p>
          The PDF and Word files use one column, standard headings and plain dates, and Leafout checks that every verified sentence can be read
          back from the text inside the file. The cover note runs 250 to 400 words, has a plain sentence for each must-have gap, and avoids generic openers.
        </p>
      </section>

      <section>
        <h2>Postings are data</h2>
        <p>
          Whatever is in a posting is treated as text to be read, not as instructions. Leafout does not follow commands hidden in it, and it does
          not open links inside it.
        </p>
      </section>

      <section>
        <h2>Read more</h2>
        <ul>
          <li>
            <a href="https://leafout.in/blog/read-a-job-posting">How to read a job posting, requirement by requirement</a>
          </li>
          <li>
            <a href="https://leafout.in/blog/why-no-ats-score">Why Leafout doesn&apos;t show an ATS score</a>
          </li>
        </ul>
        <p>
          What a match costs is on the <a href="/pricing">pricing page</a>.
        </p>
      </section>
    </>
  ),
};
