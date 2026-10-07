import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './theme/tokens.css';
import './styles/global.css';
import { startAmbient } from './theme/ambient';
import { App } from './App';

// The boot script in <head> painted the first hue; this takes over the 7 s stepper.
startAmbient();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
