import { Contact } from 'tayyari';
import { PfBlock } from './_shared';

/** `signOff`: a closing line with the links. Signature look. */
export const SignOff = () => <PfBlock look={0} kind="contact" as={Contact} />;

/** `links`: just the links. Builder look. */
export const Links = () => <PfBlock look={2} kind="contact" as={Contact} />;
