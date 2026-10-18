/** "Read this as text": paragraphs written in the browser from the blocks, so the text never says more than they do. */
export function ReadAsText({ paragraphs }: { paragraphs: string[] }) {
  return (
    <details className="pp-text">
      <summary className="pp-text-summary">Read this as text</summary>
      <div className="pp-text-body">
        {paragraphs.map((p, i) => (
          <p key={i}>{p}</p>
        ))}
      </div>
    </details>
  );
}
