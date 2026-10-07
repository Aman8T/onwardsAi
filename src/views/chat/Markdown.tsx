/**
 * Assistant markdown on the calm field: plain text at body size with generous spacing, serif
 * h2/h3, hairline code blocks and tables in a scroll box. While streaming, a soft caret is
 * appended inside the last block (via a tiny remark plugin).
 */
import { memo, useDeferredValue, useEffect, useRef, useState, type ComponentProps, type JSX, type ReactNode } from 'react';
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { IconCheck } from '../../components/Icons';

/* ───────────── streaming caret ───────────── */

interface MNode {
  type: string;
  children?: MNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

/** Appends a caret node to the deepest last block so it sits at the end of the text. */
function remarkCaret() {
  return (tree: unknown) => {
    let host = tree as MNode;
    while (host.children?.length) {
      const last = host.children[host.children.length - 1];
      if (!last.children) break;
      host = last;
    }
    host.children ??= [];
    host.children.push({
      type: 'tayyariCaret',
      data: { hName: 'span', hProperties: { className: ['ch-caret'], ariaHidden: 'true' } },
    });
  };
}

type Plugins = ComponentProps<typeof ReactMarkdown>['remarkPlugins'];
const PLUGINS: Plugins = [remarkGfm];
const STREAM_PLUGINS: Plugins = [remarkGfm, remarkCaret];

/* ───────────── hast helpers ───────────── */

interface HNode {
  type?: string;
  value?: string;
  children?: HNode[];
  properties?: { className?: unknown };
}

function hastText(node: unknown): string {
  const n = node as HNode | undefined;
  if (!n) return '';
  if (n.type === 'text') return n.value ?? '';
  return (n.children ?? []).map(hastText).join('');
}

function langOf(node: unknown): string {
  const code = (node as HNode | undefined)?.children?.[0];
  const cls = code?.properties?.className;
  const list = Array.isArray(cls) ? cls : typeof cls === 'string' ? cls.split(/\s+/) : [];
  const hit = list.find((c): c is string => typeof c === 'string' && c.startsWith('language-'));
  return hit ? hit.slice('language-'.length) : '';
}

/* ───────────── code block ───────────── */

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setDone(false), 1600);
    } catch {
      /* clipboard unavailable — nothing to do */
    }
  };
  return (
    <button type="button" className="btn btn-quiet btn-sm ch-copy" onClick={copy} aria-label={done ? 'Copied' : 'Copy code'}>
      {done ? <IconCheck size={13} strokeWidth={1.5} /> : null}
      <span>{done ? 'Copied' : 'Copy'}</span>
    </button>
  );
}

function CodeBlock({ lang, text, children }: { lang: string; text: string; children: ReactNode }) {
  return (
    <div className="ch-codeblock">
      <div className="ch-codehead">
        <span className="kicker">{lang || 'code'}</span>
        <CopyButton text={text.replace(/\n$/, '')} />
      </div>
      <pre className="scroll" tabIndex={0}>
        {children}
      </pre>
    </div>
  );
}

/* ───────────── component map ───────────── */

type Props<T extends keyof JSX.IntrinsicElements> = JSX.IntrinsicElements[T] & ExtraProps;

const components: Components = {
  a: ({ node: _n, children, ...rest }: Props<'a'>) => (
    <a {...rest} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  pre: ({ node, children }: Props<'pre'>) => (
    <CodeBlock lang={langOf(node)} text={hastText(node)}>
      {children}
    </CodeBlock>
  ),
  table: ({ node: _n, children }: Props<'table'>) => (
    <div className="ch-tablewrap scroll" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  img: ({ node: _n, alt, ...rest }: Props<'img'>) => <img {...rest} alt={alt ?? ''} loading="lazy" />,
};

export const Markdown = memo(function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  // Deferring keeps typing/scrolling responsive while a long answer streams in.
  const shown = useDeferredValue(text);
  return (
    <div className="ch-md">
      <ReactMarkdown remarkPlugins={streaming ? STREAM_PLUGINS : PLUGINS} components={components}>
        {shown}
      </ReactMarkdown>
    </div>
  );
});
