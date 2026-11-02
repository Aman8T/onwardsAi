/**
 * The chat's Markdown renderer as its own chunk: react-markdown and remark leave the App chunk and arrive on the
 * first assistant message. Until then the raw text shows in a paragraph.
 */
import { Suspense, lazy } from 'react';

const MarkdownImpl = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })));

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <Suspense fallback={<p className="ch-md">{text}</p>}>
      <MarkdownImpl text={text} streaming={streaming} />
    </Suspense>
  );
}
