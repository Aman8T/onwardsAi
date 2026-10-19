/**
 * "Sign in" for a page that needs it: a real link through the Leafout account when the server offers it (back to
 * `path` afterwards), otherwise a button that opens the sign-in sheet, which is how signing in works without Leafout.
 */
import type { ReactNode } from 'react';
import { currentPath, signInHref } from './links';
import { signInSheet, useMe } from './useMe';
import type { SignInReason } from './useMe';

interface Props {
  children: ReactNode;
  /** Where the person returns to; the current page when omitted. */
  path?: string;
  reason?: SignInReason;
  className?: string;
}

export function SignInLink({ children, path, reason = 'account', className = 'btn' }: Props) {
  const { me } = useMe();
  const target = path ?? currentPath();
  const href = signInHref(me, target);
  if (href) {
    return (
      <a className={className} href={href}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" className={className} onClick={() => signInSheet.open(reason, undefined, target)}>
      {children}
    </button>
  );
}
