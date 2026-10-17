import { PortfolioPage, sampleRiya } from 'tayyari';
import { settleMotion } from './_shared';

settleMotion();

const { content, graph, looks } = sampleRiya;

/** Riya Kapoor's first look: the Signature composition (split hero beside the graph), Iris family, Studio type. */
export const Signature = () => <PortfolioPage spec={looks[0]} content={content} graph={graph} mode="public" skin="standard" />;

/** Her second look: Narrative (statement hero, stellar graph), Glacier family, Editorial type. */
export const Narrative = () => <PortfolioPage spec={looks[1]} content={content} graph={graph} mode="public" skin="standard" />;

/** Her third look: Builder (index hero, ledger graph, project grid), Sage family, Technical type, compact. */
export const Builder = () => <PortfolioPage spec={looks[2]} content={content} graph={graph} mode="public" skin="standard" />;

/** The Old Newspaper skin: the same spec re-rendered as a printed page. */
export const OldNewspaper = () => <PortfolioPage spec={looks[0]} content={content} graph={graph} mode="public" skin="press" />;
