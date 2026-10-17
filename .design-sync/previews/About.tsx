import { About } from 'tayyari';
import { PfBlock } from './_shared';

/** `lede`: one strong sentence from the aboutLede slot. Signature look. */
export const Lede = () => <PfBlock look={0} kind="about" as={About} />;

/** `prose`: lede and body. Narrative look. */
export const Prose = () => <PfBlock look={1} kind="about" as={About} />;
