/**
 * The notices of the edge states the "States" board draws that belong to the screens around the room: today's limit,
 * practice paused, the sample profile, a short resume, a posting with text aimed at an AI, and no work sample for this
 * kind of work. Each is one calm panel: the sentence, then what the person can do. The wording is the board's, word for
 * word; the room's own edges (a blocked microphone, silence, a dropped connection) are drawn by the room.
 *
 * Exported so the plan screen can show `LimitNotice`, `PausedNotice`, `ThinNote` and `NoTemplateNote` on the same words.
 */
import type { ReactNode } from 'react';
import { FREE_LINE, UNLIMITED_LINE } from '../words';

/** How many practice interviews a person has each day while this is free. */
export const DAILY_INTERVIEWS = 3;

interface NoticeProps {
  children: ReactNode;
  /** Buttons, if any. */
  actions?: ReactNode;
  /** A picture of counts above the sentence. */
  lead?: ReactNode;
  /** An error is read out at once; everything else waits to be read. */
  alert?: boolean;
}

export function Notice({ children, actions, lead, alert = false }: NoticeProps) {
  return (
    <section className="panel pc-notice" role={alert ? 'alert' : 'status'}>
      {lead}
      <div className="pc-notice-text">{children}</div>
      {actions && <div className="pc-actions">{actions}</div>}
    </section>
  );
}

/** "You've used today's 3 practice interviews." The squares are the day's interviews, all used. */
export function LimitNotice({ used = DAILY_INTERVIEWS }: { used?: number }) {
  return (
    <Notice
      lead={
        <span className="pc-squares" role="img" aria-label={`${used} of ${used} practice interviews used today`}>
          {Array.from({ length: used }, (_, i) => (
            <span key={i} className="pc-sq" aria-hidden="true" />
          ))}
        </span>
      }
    >
      <p>
        You've used today's {used} practice interviews. They're free while we test them. More tomorrow. Your debriefs stay here.
      </p>
    </Notice>
  );
}

export function PausedNotice() {
  return (
    <Notice>
      <p>Practice is paused for today while we keep costs in check. Your debriefs are still here.</p>
    </Notice>
  );
}

export function SampleNotice({ onAnalyse }: { onAnalyse: () => void }) {
  return (
    <Notice
      actions={
        <button type="button" className="btn btn-primary pc-btn" onClick={onAnalyse}>
          Analyse my resume
        </button>
      }
    >
      <p>This is the sample profile's interview. Analyse your own resume to practise on your own work.</p>
    </Notice>
  );
}

/** The plan's own sentence when a profile is short ("…so this interview has 3 questions, not 5. …"), with a way to fix it. */
export function ThinNote({ note, onAdd }: { note?: string; onAdd: () => void }) {
  return (
    <Notice
      actions={
        <button type="button" className="btn btn-primary pc-btn" onClick={onAdd}>
          Add to my profile
        </button>
      }
    >
      <p>{note ?? 'Your profile is short, so this interview has 3 questions, not 5. Add a project or a link to get more.'}</p>
    </Notice>
  );
}

/** A posting is data, never instructions: text aimed at an AI is quoted as it was found, and left out. */
export function InjectedNote({ quote }: { quote: string }) {
  return (
    <Notice>
      <p>
        This posting has text aimed at an AI: “{quote}”. We left it out and skipped the question it touched.
      </p>
      <p className="pc-cite">Posting</p>
    </Notice>
  );
}

export function NoTemplateNote({ onRole }: { onRole: () => void }) {
  return (
    <Notice
      actions={
        <button type="button" className="btn btn-primary pc-btn" onClick={onRole}>
          Practise This role
        </button>
      }
    >
      <p>There's no work sample for this kind of role yet. Practise This role instead, built from the posting.</p>
    </Notice>
  );
}

export function ErrorNote({ message, onRetry, onBack }: { message: string; onRetry?: () => void; onBack?: () => void }) {
  return (
    <Notice
      alert
      actions={
        <>
          {onRetry && (
            <button type="button" className="btn btn-primary pc-btn" onClick={onRetry}>
              Try again
            </button>
          )}
          {onBack && (
            <button type="button" className="btn btn-quiet pc-btn" onClick={onBack}>
              Back to Practice
            </button>
          )}
        </>
      }
    >
      <p>{message}</p>
    </Notice>
  );
}

/**
 * "Free while we test it · 3 a day · 2 left today": what practice costs now, and what is left of today. An account with
 * no limits has no count to give, so it reads "Unlimited for this account".
 */
export function freeLine(leftToday: number | undefined, unlimited = false): string {
  if (unlimited) return UNLIMITED_LINE;
  if (leftToday === undefined) return FREE_LINE;
  return `${FREE_LINE} · ${leftToday === 0 ? 'none left today' : `${leftToday} left today`}`;
}
