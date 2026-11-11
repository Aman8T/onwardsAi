/**
 * The coach beside the page. Version 1 shows the app's existing coach: a few fixed questions about the resume in
 * view, each of which opens the coach the way the dashboard's ask strip does. The coach answers from the person's
 * graph and sources and cannot change the resume; a coach that proposes a rewrite against a line is a later wave
 * (docs/resume.md, "Not in version 1"), so nothing here pretends to.
 */
import { starterQuestions } from './model';
import type { ResumeSummary } from './types';

export function CoachPanel({ resume, onAsk }: { resume?: ResumeSummary; onAsk: (question: string) => void }) {
  const questions = starterQuestions(resume);
  return (
    <section className="slip slip-pin tilt-r rs-coach" aria-labelledby="rs-ask">
      <h2 id="rs-ask" className="kicker">
        Ask · about this resume
      </h2>
      <p className="rs-small">The coach reads your graph and your sources, and says where each answer comes from.</p>
      <div className="rs-ask-qs">
        {questions.map((q) => (
          <button key={q} type="button" className="rs-ask-q" onClick={() => onAsk(q)}>
            {q}
          </button>
        ))}
      </div>
      <p className="rs-fine rs-coach-foot">A question opens the coach in its own view. It answers; it does not change this resume. Your draft stays as you left it.</p>
    </section>
  );
}
