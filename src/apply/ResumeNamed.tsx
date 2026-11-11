/**
 * Which resume goes out, named (docs/auto-apply.md "Which resume"): the person's default resume on `/resume`, by its title
 * and the date it was finalised, with the way to `/resume`; and the frozen copy an application was prepared with. A draft is
 * never named here: the server offers only a finalised default. The `.stamp` "default" is the kit's mark for the person's
 * own resume (design.md 7b).
 */
import type { ResumeRef } from '../../shared/apply';
import { shortDate } from '../billing/format';

export const RESUME_PATH = '/resume';

/** "11 Nov", or nothing when the date cannot be read. */
export function finalisedDate(ref: Pick<ResumeRef, 'finalisedAt'>): string {
  return shortDate(ref.finalisedAt);
}

/** "Your default resume: Resume, finalised 11 Nov" */
export function defaultLine(ref: ResumeRef): string {
  const on = finalisedDate(ref);
  return `Your default resume: ${ref.title}${on ? `, finalised ${on}` : ''}`;
}

/** "Sent with: Resume, as finalised 11 Nov" */
export function sentWithLine(ref: ResumeRef): string {
  const on = finalisedDate(ref);
  return `${ref.title}${on ? `, as finalised ${on}` : ''}`;
}

/** The frozen copy of an application as a PDF (`GET /api/apply/applications/{id}/resume-copy`). */
export function copyHref(applicationId: string): string {
  return `/api/apply/applications/${encodeURIComponent(applicationId)}/resume-copy`;
}

export function DefaultStamp() {
  return <span className="stamp">default</span>;
}

export function ResumeLink({ children = 'Open your resumes' }: { children?: string }) {
  return <a href={RESUME_PATH}>{children}</a>;
}

export function CopyLink({ applicationId, children = 'See the frozen copy (PDF)' }: { applicationId: string; children?: string }) {
  return (
    <a href={copyHref(applicationId)} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}
