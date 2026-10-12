/**
 * The account control in the headers (the graph screen and Home).
 *
 * Signed out: a quiet "Sign in" button that opens the sign-in sheet (nothing at all when the server offers no way to
 * sign in). Signed in: a hairline circle with the person's initials that opens a small menu: who you are, "Your
 * profiles" (open any of them) and "Sign out". The menu follows the app's keyboard pattern (arrows move, Escape
 * closes and refocuses the button, a pointer outside closes) and becomes a bottom sheet under 760 px.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { MeEntitlements, MeProfile, MeUser } from '../../shared/auth';
import { entitlementText } from '../billing/format';
import { NARROW_QUERY, initials, timeAgo, useMediaQuery } from '../lib/ui';
import { actions, useApp } from '../store';
import { Sheet } from './Sheet';
import { signInSheet, useMe } from './useMe';
import '../styles/auth.css';
import '../styles/billing.css';

const PROFILES_SHOWN = 8;

export function AccountMenu({ className }: { className?: string }) {
  const { user, profiles, entitlements, providers, loaded, signOut } = useMe();
  const onPricing = useApp((s) => s.view === 'pricing');
  if (!loaded) return null;
  if (!user) {
    if (!providers.google && !providers.magic) return null;
    return (
      <>
        {/* the plans are open to everyone; the link makes room for the account control on a phone */}
        {!onPricing && (
          <button type="button" className="btn btn-quiet bl-header-link" onClick={() => actions.openPricing()}>
            Pricing
          </button>
        )}
        <button type="button" className={['btn btn-quiet auth-signin', className].filter(Boolean).join(' ')} onClick={() => signInSheet.open('account')}>
          Sign in
        </button>
      </>
    );
  }
  return <Account user={user} profiles={profiles} entitlements={entitlements} signOut={signOut} className={className} />;
}

interface AccountProps {
  user: MeUser;
  profiles: MeProfile[];
  entitlements: MeEntitlements;
  signOut: () => Promise<void>;
  className?: string;
}

function Account({ user, profiles, entitlements, signOut, className }: AccountProps) {
  const narrow = useMediaQuery(NARROW_QUERY);
  const currentId = useApp((s) => s.profile?.id);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const nameId = useId();

  const name = user.name?.trim() || user.email.split('@')[0];

  // A pointer anywhere outside the control closes the dropdown (the sheet has its own scrim).
  useEffect(() => {
    if (!open || narrow) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open, narrow]);

  // The menu's items, in page order.
  const menuItems = () => Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? []);

  // Opening the dropdown moves focus onto its first item.
  useEffect(() => {
    if (open && !narrow) menuItems()[0]?.focus();
  }, [open, narrow]);

  const close = (refocus: boolean) => {
    setOpen(false);
    setProblem(null);
    if (refocus) button.current?.focus();
  };

  const openProfile = async (id: string) => {
    setBusy(id);
    setProblem(null);
    const ok = await actions.openProfile(id);
    setBusy(null);
    if (ok) close(false);
    else setProblem('That profile could not be opened. Please try again.');
  };

  const leave = async () => {
    setBusy('out');
    await signOut();
    setBusy(null);
    close(false);
  };

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const list = menuItems();
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    const focusAt = (i: number) => list[(i + list.length) % list.length]?.focus();
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        focusAt(at < 0 ? 0 : at + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        focusAt(at < 0 ? list.length - 1 : at - 1);
        break;
      case 'Home':
        e.preventDefault();
        focusAt(0);
        break;
      case 'End':
        e.preventDefault();
        focusAt(list.length - 1);
        break;
      case 'Escape':
        e.preventDefault();
        close(true);
        break;
      case 'Tab':
        setOpen(false);
        break;
    }
  };

  const panel = (asMenu: boolean) => {
    const role = asMenu ? 'menuitem' : undefined;
    return (
      <>
        <p className="kicker">Signed in</p>
        <p className="auth-menu-name" id={nameId}>
          {name}
        </p>
        <p className="auth-menu-email">{user.email}</p>

        {profiles.length > 0 && (
          <div role={asMenu ? 'group' : undefined} aria-label="Your profiles" className="auth-menu-group">
            <p className="kicker auth-menu-head">Your profiles</p>
            {profiles.slice(0, PROFILES_SHOWN).map((p) => (
              <button
                key={p.id}
                type="button"
                role={role}
                tabIndex={asMenu ? -1 : undefined}
                className="auth-item"
                aria-current={p.id === currentId ? 'true' : undefined}
                disabled={busy !== null}
                onClick={() => void openProfile(p.id)}
              >
                <span className="auth-item-label">{p.personName || p.goal || 'Profile'}</span>
                <span className="auth-item-sub">{busy === p.id ? 'Opening…' : timeAgo(Date.parse(p.createdAt))}</span>
              </button>
            ))}
          </div>
        )}

        <div role={asMenu ? 'group' : undefined} aria-label="Plans" className="bl-menu-pricing">
          <button
            type="button"
            role={role}
            tabIndex={asMenu ? -1 : undefined}
            className="auth-item"
            onClick={() => {
              close(false);
              actions.openPricing();
            }}
          >
            <span className="auth-item-label">Pricing</span>
            <span className="auth-item-sub">{entitlementText(entitlements)}</span>
          </button>
        </div>

        <p className="auth-error" role="alert">
          {problem}
        </p>

        <button type="button" role={role} tabIndex={asMenu ? -1 : undefined} className="auth-item auth-item-out" disabled={busy !== null} onClick={() => void leave()}>
          <span className="auth-item-label">{busy === 'out' ? 'Signing out…' : 'Sign out'}</span>
        </button>
      </>
    );
  };

  return (
    <div ref={wrap} className={['auth-account', className].filter(Boolean).join(' ')}>
      <button
        ref={button}
        type="button"
        className="numeral auth-avatar"
        aria-label={`Account menu, ${name}`}
        aria-haspopup={narrow ? 'dialog' : 'menu'}
        aria-expanded={open}
        aria-controls={open && !narrow ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {initials(name)}
      </button>

      {open && !narrow && (
        <div ref={menu} id={menuId} role="menu" aria-labelledby={nameId} className="auth-menu" onKeyDown={onMenuKeyDown}>
          {panel(true)}
        </div>
      )}

      {narrow && (
        <Sheet open={open} onClose={() => close(true)} labelledBy={nameId} className="auth-menu-sheet">
          {panel(false)}
        </Sheet>
      )}
    </div>
  );
}
