/**
 * The Suspense fallback for a lazy screen (src/App.tsx): the frame's shape as still tone, a header band
 * and two blocks, so a loading chunk never leaves the view blank. Nothing animates (design.md §2: zero
 * idle frames); the sr-only word is what a screen reader gets. Styles: .view-skel* in styles/global.css.
 */
export function ViewSkeleton() {
  return (
    <div className="view-skel" aria-busy="true">
      <span className="sr-only">Loading</span>
      <div className="view-skel-head" aria-hidden="true" />
      <div className="view-skel-body" aria-hidden="true">
        <div className="view-skel-block" />
        <div className="view-skel-block" />
      </div>
    </div>
  );
}
