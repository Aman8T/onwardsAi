import { Hero } from 'tayyari';
import { PfBlock } from './_shared';

/** `split`: the name beside the graph (PortfolioView places the graph in the band). Signature look. */
export const Split = () => <PfBlock look={0} kind="hero" as={Hero} />;

/** `statement`: the huge name. Narrative look. */
export const Statement = () => <PfBlock look={1} kind="hero" as={Hero} />;

/** `index`: a small name, the tagline as the headline. Builder look. */
export const Index = () => <PfBlock look={2} kind="hero" as={Hero} />;
