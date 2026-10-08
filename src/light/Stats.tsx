/**
 * Dev-only readout for the cursor light (`?light=stats`): frame intervals, GPU time, how far the heart sits
 * behind the pointer, the render scale and the lowest lightness drawn. Everything shown is measured by the
 * engine; this component only formats it.
 */
import type { LightStats } from './engine';

const ms = (v: number | null): string => (v === null ? 'n/a' : v.toFixed(2));

export function LightStatsOverlay({ stats: s }: { stats: LightStats }) {
  const lines = [
    `light   ${s.state}  alpha ${s.alpha.toFixed(2)}  frames ${s.frames}`,
    `frame   p50 ${s.p50.toFixed(1)} ms  p95 ${s.p95.toFixed(1)}  max ${s.max.toFixed(1)}`,
    `gpu     ${ms(s.gpuMs)} ms  (max ${ms(s.gpuMax)})   cpu/tick ${s.cpuMs.toFixed(2)} ms  (max ${s.cpuMax.toFixed(2)})`,
    `backing ${s.backing[0]}×${s.backing[1]}  scale ${s.scale}  dpr ${window.devicePixelRatio}`,
    `follow  ${s.speed.toFixed(0)} px/s  stretch ${s.stretch.toFixed(2)}  behind ${s.lagPx.toFixed(0)} px  (max ${s.maxLagPx.toFixed(0)} px, ${s.lagMs.toFixed(0)} ms)`,
    `colour  hue ${s.hue.toFixed(0)}  field ${s.fieldHue}  min L ${s.minL === null ? 'n/a' : s.minL.toFixed(3)}`,
    `heap    ${s.heapMb === null ? 'n/a' : `${s.heapMb.toFixed(1)} MB`}`,
  ];
  return <pre className="light-stats">{lines.join('\n')}</pre>;
}
