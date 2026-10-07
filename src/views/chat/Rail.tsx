import type { ChatOrigin, Profile } from '../../../shared/types';
import type { Thread } from '../../store';
import { IconMinus, IconPlus } from '../../components/Icons';
import { timeAgo } from '../../lib/ui';
import { OriginCard } from './OriginCard';
import { THREAD_KIND, threadTitle } from './helpers';

interface RailProps {
  profile: Profile;
  origin: ChatOrigin;
  threads: Thread[];
  activeId: string | null;
  now: number;
  /** Narrow screens only: the rail collapses behind a toggle. */
  open: boolean;
  onToggle: () => void;
  onOrigin: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
}

/** A quiet list: where the thread started, what Tayyari can see, and the threads themselves. */
export function Rail({ profile, origin, threads, activeId, now, open, onToggle, onOrigin, onSelect, onNew }: RailProps) {
  return (
    <aside className={`ch-rail${open ? ' is-open' : ''}`} aria-label="Context and threads">
      <button type="button" className="btn ch-rail-toggle" onClick={onToggle} aria-expanded={open} aria-controls="ch-rail-body">
        <span className="ch-rail-toggle-t">
          Context &amp; threads
          <span className="numeral ch-rail-count">{threads.length}</span>
        </span>
        {open ? <IconMinus size={16} strokeWidth={1.5} /> : <IconPlus size={16} strokeWidth={1.5} />}
      </button>

      <div className="ch-rail-body scroll" id="ch-rail-body">
        <section className="ch-sec">
          <h2 className="kicker">Started from</h2>
          <OriginCard origin={origin} profile={profile} onOpen={onOrigin} />
        </section>

        <section className="ch-sec">
          <h2 className="kicker">Tayyari can see</h2>
          <ul className="ch-see">
            {profile.sources.map((s) => (
              <li key={s.id} className={s.ok ? undefined : 'is-failed'} title={s.ok ? undefined : s.error}>
                <span className="ch-see-name">{s.label}</span>
                {s.sub ? <span className="ch-see-sub">{s.sub}</span> : null}
                {!s.ok && <span className="sr-only"> (could not be read)</span>}
              </li>
            ))}
            <li>
              <span className="ch-see-name">Skill graph</span>
              <span className="ch-see-sub">{`${profile.skills.length} skills, ${profile.roles.length} roles`}</span>
            </li>
          </ul>
        </section>

        <section className="ch-sec ch-sec--threads">
          <h2 className="kicker">Threads</h2>
          <button type="button" className="btn ch-new" onClick={onNew}>
            <IconPlus size={14} strokeWidth={1.5} />
            <span>New question</span>
          </button>
          {threads.length > 0 && (
            <ul className="ch-threads">
              {threads.map((t) => {
                const live = t.messages.some((m) => m.status === 'streaming');
                const on = t.id === activeId;
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      className={`ch-thread${on ? ' on' : ''}`}
                      aria-current={on ? 'true' : undefined}
                      onClick={() => onSelect(t.id)}
                    >
                      <span className="ch-thread-title">
                        {live && (
                          <>
                            <span className="ch-live" aria-hidden="true" />
                            <span className="sr-only">Answering: </span>
                          </>
                        )}
                        {threadTitle(t)}
                      </span>
                      <span className="kicker ch-thread-meta">
                        {THREAD_KIND[t.origin.kind]} · {live ? 'now' : timeAgo(t.createdAt, now)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
