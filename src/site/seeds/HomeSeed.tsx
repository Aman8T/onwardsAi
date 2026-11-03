import { jobMatch } from '../pages/JobMatch';
import { about } from '../pages/About';
import { OPERATOR_LINE } from '../links';
import type { Faq } from '../types';

/** The question of `faq` that starts with `q`; the build stops when a page's wording changes and this one goes stale. */
function entry(faq: readonly Faq[], q: string): Faq {
  const found = faq.find((f) => f.q === q);
  if (!found) throw new Error(`HomeSeed: no FAQ entry "${q}"`);
  return found;
}

/** Questions on the Home shell: the same words as the content pages, so nothing is said twice in two ways. */
export const HOME_FAQ: readonly Faq[] = [
  entry(about.faq, 'Who is Leafout for?'),
  entry(about.faq, 'Does Leafout scrape LinkedIn?'),
  entry(jobMatch.faq, 'Does Leafout give an ATS score?'),
];

/**
 * What a crawler that does not run JavaScript reads on the Home shell, inside <noscript> (the app's own screen replaces it
 * for everyone else). Plain text: what the product is, who it is for, the flow, a line on India, a line on cost.
 */
export function HomeSeed() {
  return (
    <>
      <h1>See where you stand. Then where you could go.</h1>
      <p>
        Leafout reads your resume, and your GitHub, deployed apps and public links when you have them, and shows your skills as a graph. Every skill cites
        the line, link or repository it came from, so you and anyone you show it to can check.
      </p>
      <p>
        It is for students in India and for working professionals of every profession, in India and elsewhere. A resume is enough to start. {OPERATOR_LINE}
      </p>
      <h2>How it works</h2>
      <ol>
        <li>Add a resume, and any links you have. Or open the sample profile, a fictional person, to see the whole flow first.</li>
        <li>
          See your <a href="/skill-graph">skill graph</a>: the skills with their evidence, and the roles you are closest to.
        </li>
        <li>
          Paste a job posting and see <a href="/job-match">every requirement checked against your evidence</a>, then get a resume and cover note built
          only from facts you can cite.
        </li>
        <li>
          Follow a <a href="/career-plan">plan</a> for the gaps, and <a href="/mock-interview">practise the interview</a> on your own work.
        </li>
      </ol>
      <h2>Built for India first</h2>
      <p>
        Students are served directly, not through their colleges. There is no annual lock-in: nobody has to buy a year of anything to find out whether it
        helps. Prices for India are in rupees, and you can pay by UPI, card or net banking.
      </p>
      <h2>What it costs</h2>
      <p>
        The skill graph and your next roles are free, and so is one posting match each calendar month. After that you can pay per posting or for 30 days,
        once, with no renewals. See <a href="/pricing">pricing</a>.
      </p>
      <h2>Questions</h2>
      {HOME_FAQ.map((f) => (
        <div key={f.q}>
          <h3>{f.q}</h3>
          <p>{f.a}</p>
        </div>
      ))}
    </>
  );
}
