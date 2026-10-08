/**
 * Contact: links the person gave, plus email / phone only when `spec.privacy` allows each.
 * Variants: signOff (a big "Say hello." over the links) and links (one row; a hairline bar with
 * monospace links in the Ledger composition).
 */
import { BlockFrame, EmptyBlock, ExtLink, SectionShell, contactItems, usePf } from './SectionShell';
import type { BlockProps, ContactItem } from './SectionShell';

function Links({ items, mono }: { items: ContactItem[]; mono?: boolean }) {
  return (
    <ul className={mono ? 'pf-contact-links pf-contact-links--mono' : 'pf-contact-links'}>
      {items.map((it) => (
        <li key={it.key}>
          {it.href ? (
            <ExtLink url={it.href} label={it.text} className="pf-contact-link" />
          ) : (
            <span className="pf-contact-text">
              <span className="pf-contact-k">{it.label}</span> {it.text}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function Contact({ block, index }: BlockProps) {
  const { content, spec, uid } = usePf();
  const items = contactItems(content, spec);
  if (!items.length) return <EmptyBlock block={block} index={index} label="Contact" />;

  if (block.variant === 'links' && spec.composition === 'ledger') {
    return (
      <SectionShell block={block} index={index} label="Contact" className="pf-contact pf-contact--bar">
        <Links items={items} mono />
      </SectionShell>
    );
  }

  const headId = `${uid}-contact-h`;
  const row = block.variant === 'links';
  return (
    <BlockFrame block={block} index={index} labelledBy={headId} className={row ? 'pf-contact pf-contact--row' : 'pf-contact pf-contact--sign'}>
      <h2 id={headId} className="pf-sign">
        Say hello.
      </h2>
      <Links items={items} />
    </BlockFrame>
  );
}
