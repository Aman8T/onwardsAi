import type { ContentPage } from '../types';

export const skillGraph: ContentPage = {
  path: '/skill-graph',
  title: 'Skill graph from your resume, with evidence',
  description:
    'Leafout shows your skills as a graph where every skill cites the line of your resume, repository or page it came from. Scores are readings, not grades.',
  h1: 'Your skills as a graph, each one cited',
  lede: 'A resume lists skills. A Leafout skill graph shows them with proof: every skill carries the lines from your resume, GitHub or public pages that support it, so you and anyone you show it to can check.',
  blurb: 'Domains and skills with the evidence behind every score.',
  cta: { label: 'Build my skill graph', href: '/' },
  related: ['/how-it-works', '/job-match', '/career-plan'],
  faq: [
    {
      q: 'What does a skill score mean?',
      a: 'It is Leafout’s reading of how strongly your sources show the skill, from 0 to 100. It is not a grade, a test result or a comparison with other people, and Leafout does not quote percentiles.',
    },
    {
      q: 'Where does the evidence for a skill come from?',
      a: 'Only from your own sources: lines of your resume, files and READMEs in your repositories, pages of apps you deployed, and links you connected. Each evidence line is tagged with its source.',
    },
    {
      q: 'What if a skill is missing from my graph?',
      a: 'Add the source that shows it, such as a repository or a page, and run the analysis again. A skill that no source supports is left out rather than guessed.',
    },
    {
      q: 'Is the skill graph only for software people?',
      a: 'No. The graph is built from whatever you have done, so a teacher’s graph and an engineer’s graph have different domains. The resume is enough to start.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>What a skill graph is</h2>
        <p>
          Leafout reads your resume and any other sources you connect, then groups what it finds into four to seven domains: the main areas of work
          in your own career. Inside each domain are the skills. The domains come from what you have done, not from a fixed list, so a marketer&apos;s
          graph and an engineer&apos;s graph have different axes.
        </p>
        <p>The graph is drawn as a radar. Select a domain to see its skills, and select a skill to see exactly what supports it.</p>
      </section>

      <section>
        <h2>What each skill carries</h2>
        <ul>
          <li>A name and the domain it belongs to.</li>
          <li>A score from 0 to 100, Leafout&apos;s reading of how strongly your sources show it.</li>
          <li>
            One or more evidence lines, each tagged with where it came from: your resume, your GitHub, an app you built, your portfolio or another
            link you connected.
          </li>
          <li>The roles the skill would strengthen, so you can see why it matters.</li>
        </ul>
        <p>
          That last link between a skill and a role is what the next-roles view uses. It lays the role&apos;s requirements over your graph, so you see
          what you already show and what you do not.
        </p>
      </section>

      <section>
        <h2>Reading the chart</h2>
        <p>
          The axes stay in a fixed order and are never sorted by value, so the shape of your skills becomes familiar the more you look at it. When you
          flip to a role, you appear as the darker fill and the role as a lighter one, so it is easy to see where the role asks for more.
        </p>
        <p>
          Every chart comes with a plain data table for screen readers, and the points on it can be reached and opened with the keyboard.
        </p>
      </section>

      <section>
        <h2>How the graph is kept honest</h2>
        <p>
          Before you see the graph, each claim is compared with the text it cites: names, numbers and identifiers have to appear there. A claim that
          the text does not support is dropped. The scores are a reading by Leafout, and they are labelled that way. In interview practice, a skill
          Leafout rates low is never asked about as if your resume had claimed it.
        </p>
        <p>
          Leafout does not score personality, culture fit or coachability. Those cannot be read from documents, so they are not part of the graph.
        </p>
      </section>

      <section>
        <h2>What you can do with it</h2>
        <ul>
          <li>
            See the <a href="/how-it-works">roles you are closest to</a>, with what you have and what you lack for each.
          </li>
          <li>
            Check a <a href="/job-match">job posting</a> against the same evidence.
          </li>
          <li>
            Ask &quot;How do I prepare for this role?&quot; and get a <a href="/career-plan">plan</a> built from the gaps.
          </li>
          <li>
            Turn it into a <a href="/resume-to-portfolio">portfolio page</a>, or share the radar as a picture for a GitHub or LinkedIn profile.
          </li>
          <li>Select any node and ask the chat to help you prepare for that topic, or to improve your GitHub.</li>
        </ul>
        <p>
          The graph is most useful to people whose employer name does not do the talking for them: students, people changing field, anyone outside a
          famous company. It shows what you did, no more and no less, and you can point to the line that proves it.
        </p>
      </section>
    </>
  ),
};
