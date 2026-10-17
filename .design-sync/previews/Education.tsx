import { Education } from 'tayyari';
import { PfBlock } from './_shared';

/** `row`: degree, institution, years, with the final-year project. Signature look. */
export const Row = () => <PfBlock look={0} kind="education" as={Education} />;

/** `compact`: one line. Builder look. */
export const Compact = () => <PfBlock look={2} kind="education" as={Education} />;
