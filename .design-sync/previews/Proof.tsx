import { Proof } from 'tayyari';
import { PfBlock } from './_shared';

/** The proof strip: numbers from public sources (stars, repos, case studies), each with its source. */
export const Strip = () => <PfBlock look={0} kind="proof" as={Proof} />;

/** The same strip in the Builder look (Sage, Technical, compact). */
export const StripBuilder = () => <PfBlock look={2} kind="proof" as={Proof} />;
