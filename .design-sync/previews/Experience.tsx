import { Experience } from 'tayyari';
import { PfBlock } from './_shared';

/** `timeline`: roles in order with their highlights. Signature look. */
export const Timeline = () => <PfBlock look={0} kind="experience" as={Experience} />;

/** `ledger`: a denser table-like reading. Builder look. */
export const Ledger = () => <PfBlock look={2} kind="experience" as={Experience} />;
