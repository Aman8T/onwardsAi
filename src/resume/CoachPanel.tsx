/**
 * The coach beside the page, as a post-it stuck in the margin next to the resume (in flow: it sits in its own column on
 * a wide screen and under the sheet on a phone, so it never covers a line). Version 1 shows the app's existing coach: a few
 * fixed questions about the resume in view, each of which opens the coach the way the dashboard's ask strip does. The
 * coach answers from the person's graph and sources and cannot change the resume; a coach that proposes a rewrite against
 * a line is shown in the chat (RewriteCard), so nothing here pretends to.
 */
import type { CSSProperties } from 'react';
import { starterQuestions } from './model';
import type { ResumeSummary } from './types';

export function CoachPanel({ resume, onAsk }: { resume?: ResumeSummary; onAsk: (question: string) => void }) {
  const questions = starterQuestions(resume);
  return (
    <div className="rs-coach-wrap">
      <section className="stick is-flow postit rs-coach" style={{ '--pp': 'var(--paper-yellow)' } as CSSProperties} aria-labelledby="rs-ask">
        <h2 id="rs-ask" className="pen">
          Ask the coach
        </h2>
        <p className="pcite">about this resume · it reads your graph and your sources, and says where each answer comes from</p>
        <div className="rs-ask-qs">
          {questions.map((q) => (
            <button key={q} type="button" className="ptodo rs-ask-q" onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
        </div>
        <p className="pcite rs-coach-foot">A question opens the coach in its own view. It answers; it does not change this resume. Your draft stays as you left it.</p>
      </section>
    </div>
  );
}
