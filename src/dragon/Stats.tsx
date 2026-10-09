/**
 * Dev-only read-out for the dragon (`?dragon=stats`): frames, GPU and main-thread time, draw calls,
 * particles, and what the creature is doing. Everything is measured by the engine; this only formats it.
 */
import type { DragonStats } from './engine';

const ms = (v: number | null): string => (v === null ? 'n/a' : v.toFixed(2));

export function DragonStatsOverlay({ stats: s }: { stats: DragonStats }) {
  const lines = [
    `dragon  ${s.species}  ${s.stage}  age ${s.age.toFixed(0)} s  scale ${s.scale.toFixed(2)}  mood ${s.mood}`,
    `drives  hunger ${s.hunger.toFixed(2)}  fatigue ${s.fatigue.toFixed(2)}  burns ${s.burns}`,
    `frame   p50 ${s.p50.toFixed(1)} ms  p95 ${s.p95.toFixed(1)}  max ${s.max.toFixed(1)}  frames ${s.frames}`,
    `gpu     ${ms(s.gpuMs)} ms (max ${ms(s.gpuMax)})   cpu/tick ${s.cpuMs.toFixed(2)} ms (max ${s.cpuMax.toFixed(2)})`,
    `draw    calls ${s.drawCalls}  triangles ${s.triangles}  particles ${s.particles}  dpr ${s.dpr}`,
    `clips   ${s.clips}`,
    `heap    ${s.heapMb === null ? 'n/a' : `${s.heapMb.toFixed(1)} MB`}`,
  ];
  return <pre className="dragon-stats">{lines.join('\n')}</pre>;
}
