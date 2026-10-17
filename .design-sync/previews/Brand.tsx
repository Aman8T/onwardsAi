import { Brand } from 'tayyari';

/** The wordmark as plain markup: a hairline ring with a centre dot, then the serif name. */
export const Wordmark = () => <Brand />;

/** With the "Career intelligence" kicker beside it, as in the app header. */
export const WithTagline = () => <Brand tagline />;

/** As the home link: `onClick` turns it into a real button with an accessible name. */
export const HomeButton = () => <Brand tagline onClick={() => {}} />;

/** Scaled: `size` is the reference (28 is the default), everything scales with it. */
export const Sizes = () => (
  <div style={{ display: 'grid', gap: 'var(--s4)', alignItems: 'start' }}>
    <Brand size={22} />
    <Brand size={28} />
    <Brand size={40} />
  </div>
);
