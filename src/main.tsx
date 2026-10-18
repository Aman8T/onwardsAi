import { StrictMode, Suspense, lazy } from 'react';
import type { ComponentType } from 'react';
import { createRoot } from 'react-dom/client';
import './theme/tokens.css';
import './styles/global.css';
import { startAmbient } from './theme/ambient';

// The boot script in <head> painted the first hue; this takes over the 7 s stepper.
startAmbient();

// Every screen loads lazily, the app shell included. A published portfolio (`/p/<id>`, read-only, shown to
// strangers) must never load the app: `App` pulls in the store, which reads localStorage for the owner's profile id,
// so the public page's bundle has to be reachable without it. In dev builds only, `/dev/portfolios` is the gallery.
const { pathname } = window.location;
let Root: ComponentType;
if (pathname.startsWith('/p/')) {
  Root = lazy(() => import('./portfolio/PublicView').then((m) => ({ default: m.PublicView })));
} else if (import.meta.env.DEV && pathname === '/dev/portfolios') {
  Root = lazy(() => import('./portfolio/dev/Gallery').then((m) => ({ default: m.Gallery })));
} else if (import.meta.env.DEV && pathname === '/dev/match') {
  // the five posting-match screens on the stored fixtures: no server, no account
  Root = lazy(() => import('./match/dev/MatchGallery').then((m) => ({ default: m.MatchGallery })));
} else if (import.meta.env.DEV && pathname === '/dev/discover') {
  // every Discover state on stored fixtures: no server, no account
  Root = lazy(() => import('./discover/dev/DiscoverGallery').then((m) => ({ default: m.DiscoverGallery })));
} else if (import.meta.env.DEV && pathname === '/dev/practice') {
  // every Practice screen and state on stored fixtures: no server, no account
  Root = lazy(() => import('./practice/dev/DevPractice').then((m) => ({ default: m.DevPractice })));
} else if (import.meta.env.DEV && pathname === '/dev/prepare') {
  // every state of a Prepare thread on stored fixtures: no server, no account
  Root = lazy(() => import('./prepare/dev/PrepareGallery').then((m) => ({ default: m.PrepareGallery })));
} else {
  Root = lazy(() => import('./App').then((m) => ({ default: m.App })));
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Root />
    </Suspense>
  </StrictMode>,
);
