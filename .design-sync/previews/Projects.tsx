import { Projects } from 'tayyari';
import { PfBlock } from './_shared';

/** `featureList`: one featured project, the rest as a list. Signature look. */
export const FeatureList = () => <PfBlock look={0} kind="projects" as={Projects} />;

/** `list`: every project as a row. Narrative look. */
export const List = () => <PfBlock look={1} kind="projects" as={Projects} />;

/** `grid`: cards. Builder look. */
export const Grid = () => <PfBlock look={2} kind="projects" as={Projects} />;
