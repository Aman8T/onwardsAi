import { StrictMode, Suspense, lazy } from 'react';
import type { ComponentType } from 'react';
import { createRoot } from 'react-dom/client';
import './theme/tokens.css';
import './styles/global.css';
import { startAmbient } from './theme/ambient';
import { App } from './App';

// The boot script in <head> painted the first hue; this takes over the 7 s stepper.
startAmbient();

// Two routes bypass the app shell: a published portfolio (`/p/<id>`, read-only, no store) and, in dev builds
// only, the portfolio gallery. Both load lazily so the main bundle stays the app.
const { pathname } = window.location;
let Root: ComponentType = App;
if (pathname.startsWith('/p/')) {
  Root = lazy(() => import('./portfolio/PublicView').then((m) => ({ default: m.PublicView })));
} else if (import.meta.env.DEV && pathname === '/dev/portfolios') {
  Root = lazy(() => import('./portfolio/dev/Gallery').then((m) => ({ default: m.Gallery })));
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Root />
    </Suspense>
  </StrictMode>,
);
