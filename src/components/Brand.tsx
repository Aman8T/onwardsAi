import { cssVars } from '../lib/ui';
import '../styles/kit.css';

interface BrandProps {
  /** When given the brand renders as a button (home link); otherwise as plain markup. */
  onClick?: () => void;
  /** Adds the "Career intelligence" kicker beside the wordmark. */
  tagline?: boolean;
  /** Scale reference: the default 28 gives a 22px wordmark and a 20px mark. */
  size?: number;
}

/** The Tayyari wordmark: a hairline ring with a centre dot, then the serif name. */
export function Brand({ onClick, tagline = false, size = 28 }: BrandProps) {
  const style = cssVars({ '--brand-s': size / 28 });
  const content = (
    <>
      <svg className="brand-mark" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        <circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" strokeWidth="1.25" opacity="0.9" />
        <circle cx="10" cy="10" r="1.25" fill="currentColor" />
      </svg>
      <span className="serif brand-word">Leafout</span>
      {tagline && <span className="kicker brand-tag">Career intelligence</span>}
    </>
  );

  return onClick ? (
    <button type="button" className="brand" style={style} onClick={onClick} aria-label="Leafout home">
      {content}
    </button>
  ) : (
    <div className="brand" style={style}>
      {content}
    </div>
  );
}
