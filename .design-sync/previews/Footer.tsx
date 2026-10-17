import { Footer } from 'tayyari';
import { PfFrame } from './_shared';

/** The provenance footer: snapshot date and the sources the graph was scored from. Signature look. */
export const Provenance = () => (
  <PfFrame look={0}>
    <Footer />
  </PfFrame>
);

/** The same footer in the Builder look. */
export const ProvenanceBuilder = () => (
  <PfFrame look={2}>
    <Footer />
  </PfFrame>
);
