import type { ContentPage } from '../types';

export const mockInterview: ContentPage = {
  path: '/mock-interview',
  title: 'Mock interview practice on your own work',
  description:
    'Practise interviews about your actual work: speak or type, get verdicts that quote your own words and a short list of what to fix. Free while in testing.',
  h1: 'Practise interviews that ask about your actual work',
  lede: 'A list of generic questions does not prepare you for the ones you will really get, which are about your resume and the role. Tayyari’s practice interview asks about the things you have written down, listens to your answer and tells you which parts held up.',
  blurb: 'Four kinds of practice, spoken or typed, with verdicts that quote you.',
  cta: { label: 'Start practising', href: '/practice' },
  related: ['/job-match', '/career-plan', '/skill-graph'],
  faq: [
    {
      q: 'Do I need a microphone?',
      a: 'No. You can speak or type. If the server cannot transcribe your voice, the room falls back to your browser’s own speech recognition, and then to typing.',
    },
    {
      q: 'Is practice free?',
      a: 'Practice is free while it is in testing.',
    },
    {
      q: 'Is my voice recording stored?',
      a: 'Tayyari does not store or log the audio. Each spoken piece is turned into text, and only the text goes into the interview.',
    },
    {
      q: 'Does it judge my personality or confidence?',
      a: 'No. Personality, culture fit and coachability cannot be read from documents or short answers, so they are not scored. Tayyari looks at what you said about your work and whether you could defend it.',
    },
    {
      q: 'What does it ask me about?',
      a: 'Only things in your own evidence, or the posting’s own words for a specific role. A skill that Tayyari rates low is never asked about as if your resume had listed it.',
    },
  ],
  Body: () => (
    <>
      <section>
        <h2>Four kinds of practice</h2>
        <ul>
          <li>
            <strong>This role.</strong> Questions for one role: things it asks that you show, a gap, a claim from your resume and a story. The role can
            come from a <a href="/job-search">job search</a> or from the roles in your <a href="/skill-graph">skill graph</a>. You can ask to spend
            more time on a part.
          </li>
          <li>
            <strong>Defend your resume.</strong> About five lines of your own evidence, including lines with a number, a line that says you led
            something, and one from a repository or a page you built.
          </li>
          <li>
            <strong>Behavioural stories.</strong> Four stories drawn from your evidence lines that involve people and action. A good story names the
            situation, what you did and what came of it.
          </li>
          <li>
            <strong>Work sample.</strong> A small task first, then three questions about your choices. There are templates for frontend, data
            analysis, marketing, nursing, accounts and teaching, picked from the role or your title. An optional assistant is available, and Tayyari
            notes how you used it.
          </li>
        </ul>
      </section>

      <section>
        <h2>Speak or type</h2>
        <p>
          You can answer aloud or in writing. When the server has a speech service configured, Tayyari uses Sarvam AI to turn your speech into text
          and to speak the interviewer&apos;s questions. Otherwise it uses your browser&apos;s own speech, and then typing. Your browser asks for
          permission before it uses the microphone.
        </p>
        <p>
          The interview itself only ever sees text. A spoken answer is cut at each pause, sent as a short piece to be transcribed, and not stored or
          logged. When the server voice is on, the lines the interviewer is likely to say next are prepared while you answer, so the room does not stop to wait.
        </p>
      </section>

      <section>
        <h2>Verdicts that quote your words</h2>
        <p>Every answer ends in one of four verdicts: defended, shaky, couldn&apos;t defend, or not assessed.</p>
        <ul>
          <li>A verdict is kept only when it rests on a passage that is really in your answer. With no such passage the answer is &quot;not assessed&quot;.</li>
          <li>An answer to the easier version of a question is capped at shaky.</li>
          <li>A story needs a situation, an action and a result to count as defended.</li>
          <li>&quot;I don&apos;t know&quot; is recorded as exactly that, with your words quoted.</li>
          <li>If you say a question gets your resume wrong, Tayyari shows you the line it was based on.</li>
        </ul>
      </section>

      <section>
        <h2>What you get at the end</h2>
        <p>
          The debrief gives you three kinds of card. <strong>Say</strong> is a way to put an answer, checked against what you said and your sources,
          with [brackets] where only you know the detail. <strong>Avoid</strong> quotes the wording that hurt you. <strong>Fix</strong> is a task of
          between 5 and 120 minutes. The counts and the first thing to work on are worked out by code.
        </p>
        <p>
          When you defend a point well, Tayyari can keep what you said as a new fact marked &quot;from you&quot;. That fact can then support a
          later <a href="/job-match">posting match</a>.
        </p>
      </section>

      <section>
        <h2>What it does not do</h2>
        <p>
          It does not give you a percentage, rank you against other candidates or claim a cohort it does not have. It does not screen for personality.
          It is practice that tells you where your own evidence is thin, so you can work on it before the real interview.
        </p>
      </section>
    </>
  ),
};
