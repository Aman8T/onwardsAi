import { Atmosphere, Brand } from 'tayyari';

/** The Drift / Still / Off control: a quiet menu button that reads the ambient mode from storage. */
export const Control = () => <Atmosphere />;

/** Where it lives: the right end of the app header, opposite the brand. */
export const InHeader = () => (
  <header
    style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 'var(--s4)',
      padding: 'var(--s3) var(--s4)',
      borderBottom: 'var(--hair) solid var(--line)',
    }}
  >
    <Brand tagline />
    <Atmosphere />
  </header>
);
