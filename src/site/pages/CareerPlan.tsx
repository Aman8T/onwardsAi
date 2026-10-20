import type { ContentPage } from '../types';

export const careerPlan: ContentPage = {
  path: '/career-plan',
  title: 'Career plan: how to prepare for a role',
  description:
    'Get four short blocks for any role: where you stand, one move, a plan sized to your hours a week, and next actions. Built from your skill graph.',
  h1: 'How do I prepare for this role?',
  lede: 'Leafout answers with a short, structured plan instead of an essay: the gaps between your graph and the role, the one move that closes most of them, a plan sized to the hours you have, and buttons for what to do next.',
  blurb: 'Gaps, one move, a plan by hours a week and next actions.',
  cta: { label: 'Start with your skill graph', href: '/' },
  related: ['/skill-graph', '/mock-interview', '/job-match'],
  faq: [
    {
      q: 'Do I need a job posting?',
      a: 'No. The plan starts from a role in your skill graph. If you have a specific posting, use job match to check it requirement by requirement.',
    },
    {
      q: 'How are the weeks worked out?',
      a: 'By code, from the hours in each phase and the hours a week you choose: 3, 6 or 10. A model does not estimate them.',
    },
    {
      q: 'Is my progress saved?',
      a: 'Yes. The tasks you tick, the hours you chose and any link you add are saved with the plan.',
    },
    {
      q: 'Does the plan promise I will get the role?',
      a: 'No. It describes work to do. It does not predict whether you will get a role, and it never shows a predicted number for how much a move will help.',
    },
    {
      q: 'Can I still ask follow-up questions?',
      a: 'Yes. The plan is four blocks, and the chat stays available under it for anything you want to ask about the role or a gap.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>Where you stand</h2>
        <p>
          The first block shows the role&apos;s gaps as bars, from where you are today to what the role expects. The numbers come straight from your{' '}
          <a href="/skill-graph">skill graph</a> and are drawn before any model has answered. The biggest gap is marked.
        </p>
        <p>
          Each gap has a one-line reason behind a tap, with a chip that says where it comes from, for example &quot;From your resume&quot;. What you
          are already strong at is a single quiet line. The header says how close the role is as &quot;closest of your roles&quot;, never as a
          percentage.
        </p>
      </section>

      <section>
        <h2>The one move</h2>
        <p>
          Leafout proposes the move that closes most of the gap. It lists what you already have that the move builds on, each item cited, and names
          the gaps the move works on. There can be up to three moves, best first, shown one at a time. A move names its gaps and nothing more: it does
          not predict how much it will help.
        </p>
      </section>

      <section>
        <h2>Your plan</h2>
        <p>
          The plan has phases. Each phase has a title, the hours it needs, two or three tasks and one deliverable. You choose how many hours a week you
          have, 3, 6 or 10, and code works out how many weeks that takes.
        </p>
        <p>
          One phase is open at a time. Tasks are checkboxes that are saved with the plan, and you can add a link to what you shipped for a phase.
        </p>
      </section>

      <section>
        <h2>Next actions</h2>
        <p>The last block is buttons, not prompts:</p>
        <ul>
          <li>Put the plan on your calendar, as a file made in your browser.</li>
          <li>Pick the repository to start from, ranked in code from what Leafout already read of your GitHub, with nothing fetched.</li>
          <li>
            Rewrite your headline for this role. It is kept only if every name and number in it is in the evidence it cites.
          </li>
          <li>
            <a href="/mock-interview">Practise the interview</a> for this role.
          </li>
        </ul>
        <p>If you prefer to read, the same blocks are available as plain text.</p>
      </section>

      <section>
        <h2>How the plan stays honest</h2>
        <p>
          Code supplies every number: the levels, the gaps, the ranking and the hours. The model writes the reasons and the moves, and each one cites
          evidence from your graph before it may say anything. If a sentence contains a number that is not in the cited evidence or the gap figures,
          the sentence is dropped. Hours per phase are held between 1 and 40, and a plan has at most five phases of four tasks each.
        </p>
        <p>
          If you already have a posting in mind, <a href="/job-match">job match</a> checks it requirement by requirement, and{' '}
          <a href="/job-search">job search</a> finds roles from employers&apos; own boards that suit your graph.
        </p>
      </section>
    </>
  ),
};
