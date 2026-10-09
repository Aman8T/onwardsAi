/*
 * What the menu and the engine share: the chosen mode and species, and a live read-out of the creature's
 * age and stage. No three.js here, so the menu never pulls the 3D chunk.
 */
import { useSyncExternalStore } from 'react';
import type { StageId } from './growth';

export type DragonMode = 'awake' | 'asleep';

export interface DragonSnapshot {
  mode: DragonMode;
  species: string;
  stage: StageId;
  /** Age in whole seconds (the chip re-renders once a second at most). */
  age: number;
  /** True once the 3D chunk and the model are loaded. */
  ready: boolean;
}

const MODE_KEY = 'tayyari.dragon.mode';

function readMode(): DragonMode {
  try {
    return window.localStorage.getItem(MODE_KEY) === 'asleep' ? 'asleep' : 'awake';
  } catch {
    return 'awake';
  }
}

let snapshot: DragonSnapshot = { mode: readMode(), species: 'lowpoly', stage: 'egg', age: 0, ready: false };
const listeners = new Set<() => void>();

function emit(next: Partial<DragonSnapshot>): void {
  snapshot = { ...snapshot, ...next };
  for (const cb of listeners) cb();
}

export const dragonState = {
  get: (): DragonSnapshot => snapshot,
  subscribe(cb: () => void): () => void {
    listeners.add(cb);
    return () => {
      listeners.delete(cb);
    };
  },
  setMode(mode: DragonMode): void {
    try {
      window.localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* the choice just does not persist */
    }
    emit({ mode });
  },
  /** Called by the engine. */
  report(next: Partial<DragonSnapshot>): void {
    if (next.age !== undefined) next.age = Math.floor(next.age);
    const same = Object.entries(next).every(([k, v]) => (snapshot as unknown as Record<string, unknown>)[k] === v);
    if (!same) emit(next);
  },
};

/** Requests from the menu to the engine (which may not be loaded yet; the engine reads the queue on start). */
export type DragonCommand = { kind: 'species'; id: string } | { kind: 'reset' } | { kind: 'hunt' };

const commands: DragonCommand[] = [];
let commandSink: ((c: DragonCommand) => void) | null = null;

export function sendDragonCommand(c: DragonCommand): void {
  if (commandSink) commandSink(c);
  else commands.push(c);
}

export function takeDragonCommands(sink: (c: DragonCommand) => void): () => void {
  commandSink = sink;
  for (const c of commands.splice(0)) sink(c);
  return () => {
    if (commandSink === sink) commandSink = null;
  };
}

export function useDragon(): DragonSnapshot {
  return useSyncExternalStore(dragonState.subscribe, dragonState.get, dragonState.get);
}
