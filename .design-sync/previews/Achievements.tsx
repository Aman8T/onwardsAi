import { Achievements } from 'tayyari';
import { PfBlock } from './_shared';

/** `list`: each achievement as a line. Signature look. */
export const List = () => <PfBlock look={0} kind="achievements" as={Achievements} />;

/** `numbers`: the countable ones as big numerals. Builder look. */
export const Numbers = () => <PfBlock look={2} kind="achievements" as={Achievements} />;
