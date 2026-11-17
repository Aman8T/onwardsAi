import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { Suspense, lazy } from 'react';
import { SignIn } from './auth/SignIn';
import { SignInGate } from './auth/SignInGate';
import { ViewSkeleton } from './components/ViewSkeleton';
import { CursorLight } from './light/CursorLight';
import { Spot } from './light/Spot';
import { useAutoClaim, useMe } from './auth/useMe';
import { useHandoff } from './lib/useHandoff';
import { useResumeAfterSignIn } from './match/pending';
import { useApp } from './store';
import { Onboarding } from './views/Onboarding';
import { Analysis } from './views/Analysis';
import { Dashboard } from './views/Dashboard';
import { GraphView } from './views/GraphView';
import { ChatView } from './views/ChatView';

// The portfolio kit (blocks, graph variants, newspaper skin, editor) loads on first visit to the screen.
const PortfolioScreen = lazy(() => import('./portfolio/PortfolioScreen').then((m) => ({ default: m.PortfolioScreen })));
// Posting match (paste, match, resume, cover note) likewise; it needs no profile on screen (a second device has none).
const MatchRoot = lazy(() => import('./match/MatchRoot').then((m) => ({ default: m.MatchRoot })));
// Discover (roles near you that fit, and the person's own list of saved roles) likewise; it needs a profile.
const DiscoverRoot = lazy(() => import('./discover/DiscoverRoot').then((m) => ({ default: m.DiscoverRoot })));
// Practice the interview (hub, plan, room, debrief, progress) likewise; it needs a profile.
const PracticeRoot = lazy(() => import('./practice/PracticeRoot').then((m) => ({ default: m.PracticeRoot })));
// The Leafout Weekly (the reader's personal paper, its subscription and archive) likewise; it needs no profile on screen.
const WeeklyView = lazy(() => import('./weekly/WeeklyView').then((m) => ({ default: m.WeeklyView })));
// The resumes (one set of blocks arranged many ways: the resume in view, Finalise, the blocks) likewise; it needs a profile.
const ResumeRoot = lazy(() => import('./resume/ResumeRoot').then((m) => ({ default: m.ResumeRoot })));
// The pricing page (and Checkout.js, which only it loads) likewise; anyone may open it, signed in or not (it is the one screen the sign-in gate leaves open).
const PricingView = lazy(() => import('./billing/PricingView').then((m) => ({ default: m.PricingView })));

/** The one easing curve for UI motion (matches --ease in tokens.css). */
const EASE = [0.2, 0.8, 0.2, 1] as const;

export function App() {
  const view = useApp((s) => s.view);
  const profile = useApp((s) => s.profile);

  // a signed-in person's profile on screen is attached to their account as soon as there are both
  useAutoClaim();
  // a resume or links handed over from leafout.in (/?handoff=<id>): start the same analysis Home would
  useHandoff();
  // a posting pasted while signed out is matched once they are back signed in
  useResumeAfterSignIn();

  // When the server requires a sign-in, every screen but the pricing page waits for one: `pending` (the server has
  // not said yet) shows nothing of the app, so no screen flashes and is taken away; `blocked` shows the sign-in welcome.
  const { gate } = useMe();

  // dashboard/graph/chat/portfolio/discover/practice/resume need a profile; fall back to the empty dashboard without one
  const shown =
    (view === 'dashboard' || view === 'graph' || view === 'chat' || view === 'portfolio' || view === 'discover' || view === 'practice' || view === 'resume') && !profile
      ? 'onboarding'
      : view;

  const gated = shown !== 'pricing' && gate !== 'open';
  const waiting = gated && gate === 'pending';
  // one key for the welcome, whatever view lies underneath, so a change of view behind it does not replay the fade
  const frameKey = gated ? 'gate' : shown;

  return (
    <MotionConfig reducedMotion="user">
      {/* Keyboard: the first Tab offers a jump past the header into the view frame (focusable, no ring). */}
      <a className="skip" href="#app-main">
        Skip to content
      </a>
      <AnimatePresence mode="wait">
        {!waiting && (
          <motion.div
            key={frameKey}
            id="app-main"
            tabIndex={-1}
            className="app-view"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.35, ease: EASE }}
          >
            {gated && <SignInGate view={view} />}
            {!gated && shown === 'onboarding' && <Onboarding />}
            {!gated && shown === 'analysis' && <Analysis />}
            {!gated && shown === 'dashboard' && profile && <Dashboard profile={profile} />}
            {!gated && shown === 'graph' && profile && <GraphView profile={profile} />}
            {!gated && shown === 'chat' && profile && <ChatView profile={profile} />}
            {!gated && shown === 'portfolio' && profile && (
              <Suspense fallback={<ViewSkeleton />}>
                <PortfolioScreen profile={profile} />
              </Suspense>
            )}
            {!gated && shown === 'match' && (
              <Suspense fallback={<ViewSkeleton />}>
                <MatchRoot />
              </Suspense>
            )}
            {!gated && shown === 'discover' && profile && (
              <Suspense fallback={<ViewSkeleton />}>
                <DiscoverRoot profile={profile} />
              </Suspense>
            )}
            {!gated && shown === 'practice' && profile && (
              <Suspense fallback={<ViewSkeleton />}>
                <PracticeRoot profile={profile} />
              </Suspense>
            )}
            {!gated && shown === 'resume' && profile && (
              <Suspense fallback={<ViewSkeleton />}>
                <ResumeRoot profile={profile} />
              </Suspense>
            )}
            {!gated && shown === 'weekly' && (
              <Suspense fallback={<ViewSkeleton />}>
                <WeeklyView />
              </Suspense>
            )}
            {!gated && shown === 'pricing' && (
              <Suspense fallback={<ViewSkeleton />}>
                <PricingView />
              </Suspense>
            )}
          </motion.div>
        )}
      </AnimatePresence>
      {/* the sign-in sheet lives in a portal, outside the animated view frame */}
      <SignIn />
      {/* the liquid cursor: one mount for every screen, behind the view (src/light) */}
      <CursorLight />
      {/* the spotlight under the wake: quiet look in dark only (src/light/Spot.tsx) */}
      <Spot />
    </MotionConfig>
  );
}
