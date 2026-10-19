import type { ContentPage } from '../types';

export const howItWorks: ContentPage = {
  path: '/how-it-works',
  title: 'How it works: resume to cited skill graph',
  description:
    'How Tayyari turns a resume, GitHub and public links into an evidence-cited skill graph, then matches job postings, builds a plan and practises interviews.',
  h1: 'How Tayyari works',
  lede: 'Tayyari reads what you have actually done and shows it as a skill graph in which every skill cites the line it came from. That graph then drives everything else: the roles you are close to, a check of any job posting, a preparation plan and interview practice.',
  blurb: 'From your sources to a cited skill graph, and what you can do with it.',
  cta: { label: 'Try a sample profile', href: '/' },
  software: true,
  related: ['/skill-graph', '/job-match', '/mock-interview'],
  faq: [
    {
      q: 'Do I need a GitHub account?',
      a: 'No. A resume is enough. GitHub, deployed apps and public links add evidence when you have them, and the graph shows which source each piece of evidence came from.',
    },
    {
      q: 'How long does an analysis take?',
      a: 'A full analysis takes about 60 to 90 seconds. The first signals from your resume appear on screen sooner than that.',
    },
    {
      q: 'Can I try it without uploading my own resume?',
      a: 'Yes. The sample profile is a fictional person, so you can see the whole flow before you share anything of your own.',
    },
    {
      q: 'Does it only work for software jobs?',
      a: 'No. The resume is the source every profession has, so a nurse, an accountant, a marketer and an engineer go through the same steps. GitHub and deployed apps are extras for people who have them.',
    },
    {
      q: 'Which AI does Tayyari use?',
      a: 'Claude models from Anthropic read your sources and phrase the text. A decision model called Jev, from TypeSafe, makes yes/no judgements that are logged with their confidence. Code does all the scoring.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>The pipeline in one view</h2>
        <ol>
          <li>
            <strong>Sources.</strong> A resume, and GitHub, deployed apps and public links when you have them.
          </li>
          <li>
            <strong>Readers.</strong> One small model reads each source and writes down facts with the lines they came from.
          </li>
          <li>
            <strong>Skill graph.</strong> The facts become domains and skills, each with a score and its evidence.
          </li>
          <li>
            <strong>Next roles.</strong> The roles you are closest to, with what you already show and what you lack.
          </li>
          <li>
            <strong>Posting match.</strong> Paste a job posting and see every requirement against your evidence.
          </li>
          <li>
            <strong>Prepare.</strong> A plan for a role, built from the gaps in your graph.
          </li>
          <li>
            <strong>Practice.</strong> Mock interviews that ask about your own work.
          </li>
        </ol>
      </section>

      <section>
        <h2>Step 1: what you connect</h2>
        <p>You start with a resume, as a PDF or a Word file. That is enough on its own. Everything else adds evidence and sharpens the graph:</p>
        <ul>
          <li>a GitHub profile, where Tayyari reads your repositories, their READMEs and their tests;</li>
          <li>apps you have deployed, including apps built with Lovable, which it reads from the public page;</li>
          <li>any public link, such as a portfolio, a blog or a Kaggle profile;</li>
          <li>notes you paste in, for example the About section of a LinkedIn profile.</li>
        </ul>
        <p>
          Tayyari does not read LinkedIn pages, because LinkedIn blocks automated reading. It asks you to paste the text instead. It also refuses to
          open addresses on private networks, so a link cannot be used to reach anything it should not.
        </p>
      </section>

      <section>
        <h2>Step 2: the readers</h2>
        <p>
          Plain code does the first pass. It pulls the text out of PDF and Word files, calls GitHub&apos;s public interface and fetches public pages.
          Then a separate small model reads each source, all at the same time, and extracts skills, areas of work, role hints and facts. Every fact
          points to the line or passage it was taken from.
        </p>
        <p>A quick first read of your resume runs beside them, so early signals show on screen while the rest is still working.</p>
      </section>

      <section>
        <h2>Step 3: the skill graph</h2>
        <p>
          A larger model reads the readers&apos; output together with your resume and builds one graph: four to seven domains, the skills inside
          each, a score from 0 to 100 for every one, and the evidence behind every score. The score is Tayyari&apos;s reading of your sources, not a
          test result.
        </p>
        <p>
          Before you see the graph, it is checked. Each claim is compared with the text it cites, including names, numbers and identifiers, and a
          claim the text does not support is dropped. The <a href="/skill-graph">skill graph page</a> shows what you get.
        </p>
      </section>

      <section>
        <h2>Step 4: next roles</h2>
        <p>
          The same graph is compared with roles. Each role lists the requirements you already show, the ones you do not, and a rough estimate of how
          long the gap might take. Roles are ordered by how close they are to you and described in words, never as a percentage. Selecting a
          requirement opens a conversation about what it means and what to build.
        </p>
      </section>

      <section>
        <h2>Steps 5 to 7: match, prepare, practise</h2>
        <p>
          <strong>Match.</strong> Paste a job posting. Tayyari reads the posting on its own first, then checks each requirement against your evidence
          and tells you where you stand in words, never as an &quot;ATS score&quot;. It can also build a resume and a cover note from facts you can
          defend. See <a href="/job-match">job match</a>.
        </p>
        <p>
          <strong>Prepare.</strong> For any role, Tayyari turns the gaps in your graph into one next move and a plan sized to the hours you have each
          week. See <a href="/career-plan">career plan</a>.
        </p>
        <p>
          <strong>Practise.</strong> Mock interviews ask about your own work and quote your answers back to you. See{' '}
          <a href="/mock-interview">mock interview</a>.
        </p>
      </section>

      <section>
        <h2>What code does and what models do</h2>
        <p>
          Code computes: scores, weights, selection, totals and the bands in a match. Models read, classify and phrase. Whatever a model writes about
          you goes into a typed slot and is checked against the facts it cites. A sentence that is not supported is dropped, or replaced by the fact&apos;s
          own wording. Numbers are never invented: if a number would help and is missing, Tayyari asks you for it. Job postings
          and documents are treated as data, and instructions found inside them are reported, not followed.
        </p>
      </section>
    </>
  ),
};
