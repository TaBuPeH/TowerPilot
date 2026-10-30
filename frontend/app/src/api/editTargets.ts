import type { EditTargets } from '../core/editingTarget.ts';
import { entries, id, own } from '../core/read.ts';
import { readJsonItem, writeJsonItem, type KeyValueStorage } from './storage.ts';

export const EDIT_TARGETS_KEY = 'tp.editTarget.v1';

export interface StoredEditTargets {
  readonly editTargets: EditTargets;
  readonly editTargetGeneration: number;
}

/** Decode only identity/pick data. Permission is revalidated by core against a new files read. */
export function restoreEditTargets(storage: KeyValueStorage | null): StoredEditTargets {
  const targets: Record<string, { name: string; generation: number }> = {};
  const stored = readJsonItem(storage, EDIT_TARGETS_KEY);
  const generation = own(stored, 'editTargetGeneration');
  if (typeof generation !== 'number' || !Number.isSafeInteger(generation) || generation < 0)
    return { editTargets: Object.freeze({}), editTargetGeneration: 0 };
  for (const [key, value] of entries(own(stored, 'editTargets'))) {
    let parts: unknown;
    try {
      parts = JSON.parse(key);
    } catch {
      continue;
    }
    if (
      !Array.isArray(parts) ||
      parts.length !== 3 ||
      id(parts[0]) === undefined ||
      id(parts[1]) === undefined ||
      (parts[2] !== null && id(parts[2]) === undefined)
    )
      continue;
    const name = id(own(value, 'name'));
    const pickGeneration = own(value, 'generation');
    if (
      name !== undefined &&
      typeof pickGeneration === 'number' &&
      Number.isSafeInteger(pickGeneration) &&
      pickGeneration > 0 &&
      pickGeneration <= generation
    )
      targets[key] = { name, generation: pickGeneration };
  }
  return { editTargets: Object.freeze(targets), editTargetGeneration: generation };
}

export function saveEditTargets(
  storage: KeyValueStorage | null,
  targets: EditTargets,
  generation: number,
): void {
  writeJsonItem(storage, EDIT_TARGETS_KEY, {
    editTargets: targets,
    editTargetGeneration: generation,
  });
}
