import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { Suspense, lazy } from 'react';
import { SignIn } from './auth/SignIn';
import { Vine } from './components/Vine';
import { CursorLight } from './light/CursorLight';
import { useAutoClaim } from './auth/useMe';
import { useHandoff } from './lib/useHandoff';
import { useResumeAfterSignIn } from './match/pending';
import { useApp } from './store';
import { Onboarding } from './views/Onboarding';
import { Analysis } from './views/Analysis';
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
// The pricing page (and Checkout.js, which only it loads) likewise; anyone may open it, signed in or not.
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

  // graph/chat/portfolio/discover/practice need a profile; fall back to onboarding without one
  const shown = (view === 'graph' || view === 'chat' || view === 'portfolio' || view === 'discover' || view === 'practice') && !profile ? 'onboarding' : view;

  return (
    <MotionConfig reducedMotion="user">
      <AnimatePresence mode="wait">
        <motion.div
          key={shown}
          className="app-view"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.35, ease: EASE }}
        >
          {shown === 'onboarding' && <Onboarding />}
          {shown === 'analysis' && <Analysis />}
          {shown === 'graph' && profile && <GraphView profile={profile} />}
          {shown === 'chat' && profile && <ChatView profile={profile} />}
          {shown === 'portfolio' && profile && (
            <Suspense fallback={null}>
              <PortfolioScreen profile={profile} />
            </Suspense>
          )}
          {shown === 'match' && (
            <Suspense fallback={null}>
              <MatchRoot />
            </Suspense>
          )}
          {shown === 'discover' && profile && (
            <Suspense fallback={null}>
              <DiscoverRoot profile={profile} />
            </Suspense>
          )}
          {shown === 'practice' && profile && (
            <Suspense fallback={null}>
              <PracticeRoot profile={profile} />
            </Suspense>
          )}
          {shown === 'weekly' && (
            <Suspense fallback={null}>
              <WeeklyView />
            </Suspense>
          )}
          {shown === 'pricing' && (
            <Suspense fallback={null}>
              <PricingView />
            </Suspense>
          )}
        </motion.div>
      </AnimatePresence>
      {/* the sign-in sheet lives in a portal, outside the animated view frame */}
      <SignIn />
      {/* the liquid cursor: one mount for every screen, behind the view (src/light) */}
      <CursorLight />
      {/* the vine along the page's edges, growing with the scroll (the name, drawn) */}
      <Vine />
    </MotionConfig>
  );
}
