import { AnimatePresence, MotionConfig, motion } from 'motion/react';
import { Suspense, lazy } from 'react';
import { useApp } from './store';
import { Onboarding } from './views/Onboarding';
import { Analysis } from './views/Analysis';
import { GraphView } from './views/GraphView';
import { ChatView } from './views/ChatView';

// The portfolio kit (blocks, graph variants, newspaper skin, editor) loads on first visit to the screen.
const PortfolioScreen = lazy(() => import('./portfolio/PortfolioScreen').then((m) => ({ default: m.PortfolioScreen })));

/** The one easing curve for UI motion (matches --ease in tokens.css). */
const EASE = [0.2, 0.8, 0.2, 1] as const;

export function App() {
  const view = useApp((s) => s.view);
  const profile = useApp((s) => s.profile);

  // graph/chat/portfolio need a profile; fall back to onboarding without one
  const shown = (view === 'graph' || view === 'chat' || view === 'portfolio') && !profile ? 'onboarding' : view;

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
        </motion.div>
      </AnimatePresence>
    </MotionConfig>
  );
}
