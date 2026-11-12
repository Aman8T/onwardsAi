/**
 * Quiet outlines while the model works: a hairline panel on the surface tone with a label and one or two blank
 * lines. Nothing moves (no loop, no shimmer); the block fills in when its event lands.
 */

/** A blank line of text in the second surface tone. */
export function SkeletonLine({ width, tall = false }: { width: string; tall?: boolean }) {
  return <span className={`pp-skel-line${tall ? ' is-tall' : ''}`} style={{ width }} aria-hidden="true" />;
}

export function MoveSkeleton() {
  return (
    <div className="pp-block pp-skel m-gap" role="status" aria-label="The move, on its way">
      <span className="kicker">Choosing the one move…</span>
      <SkeletonLine width="82%" tall />
      <SkeletonLine width="58%" />
    </div>
  );
}

export function PlanSkeleton() {
  return (
    <div className="pp-block pp-skel m-gap" role="status" aria-label="The plan, on its way">
      <span className="kicker">Laying out the plan…</span>
      <SkeletonLine width="70%" />
    </div>
  );
}

export function StandingSkeleton() {
  return (
    <div className="pp-block pp-skel m-gap" role="status" aria-label="Where you stand, on its way">
      <span className="kicker">Reading the role on your graph…</span>
      <SkeletonLine width="64%" />
      <SkeletonLine width="78%" />
      <SkeletonLine width="52%" />
    </div>
  );
}
