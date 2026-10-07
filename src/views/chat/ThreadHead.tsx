import type { Thread } from '../../store';
import { timeAgo } from '../../lib/ui';

/**
 * Quiet head line at the top of a conversation (the board's "AI Product Engineer · 50% aligned"):
 * what the thread is about on the left, where it started and when on the right.
 */
export function ThreadHead({ thread, now }: { thread: Thread; now: number }) {
  const { origin } = thread;
  const when = timeAgo(thread.createdAt, now);

  return (
    <div className="ch-thread-head ch-in">
      <h2 className="kicker ch-th-line">{origin.kind === 'free' ? 'Open question' : `${origin.title} · ${origin.caption}`}</h2>
      <span className="kicker ch-th-when">{origin.kind === 'free' ? when : `Started from the graph · ${when}`}</span>
    </div>
  );
}
