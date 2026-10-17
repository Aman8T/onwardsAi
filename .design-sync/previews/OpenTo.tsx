import { OpenTo } from 'tayyari';
import { PfBlock } from './_shared';

/** `note`: the roles the person is open to, as a short note. Hidden on Riya's public page, shown here. */
export const Note = () => <PfBlock look={0} kind="openTo" as={OpenTo} />;
