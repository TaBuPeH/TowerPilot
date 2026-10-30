import { fromServer } from '../../copy/server.ts';
import { list, own, ownPath, text } from '../../core/read.ts';
import { viewEqual } from '../store.ts';
import type { AbilitiesTarget, AbilityEditor } from '../abilities.ts';
import { abilityModule } from '../abilities.ts';
import { abilityValue, nodeAt, abilityNode, type AbilityPath } from '../abilitiesDraft.ts';
import { settingsFieldVM } from './settingsModule.ts';
export const abilityName = (value: unknown) => fromServer('name', value);
export const abilityMessage = (value: unknown) => fromServer('message', value);
export const abilityJson = (value: unknown) =>
  fromServer('detail', JSON.stringify(value ?? null, null, 2));
/** A1 uses Collection's shared typed field conversion and rendering. */
export function abilityGlobalField(t: AbilitiesTarget | null, e: AbilityEditor | null) {
  const field = abilityModule(t?.settings)?.fields.find(
    (value) => value.path === 'demon_mode_always',
  );
  if (field === undefined || e?.family !== 'global') return null;
  const value = abilityValue(e.node);
  return settingsFieldVM(field, typeof value === 'boolean' ? { demon_mode_always: value } : {});
}
export const latestAbility = (t: AbilitiesTarget, e: AbilityEditor) =>
  e.family === 'global' ? t.settings : ownPath(t.source, 'profile', 'policies', e.family, e.id);

export const abilityFieldId = (path: AbilityPath) =>
  `abilities-field-${path.length === 0 ? 'policy' : path.map(String).map(encodeURIComponent).join('-')}`;
export interface AbilityProblem {
  readonly message: NonNullable<ReturnType<typeof abilityMessage>>;
  readonly path: AbilityPath;
  readonly fieldId: string;
}
/** Read the server's own problem paths, retaining each error until that submitted value changes. */
export function abilityProblems(e: AbilityEditor | null): readonly AbilityProblem[] {
  if (e === null || e.submission === null || !['refused', 'conflict'].includes(e.outcome ?? ''))
    return [];
  const prefix = `policies.${e.family}.${e.id}`;
  const problems = list(own(e.detail, 'problems')) ?? [own(e.detail, 'error')];
  const sent = abilityNode(e.submission.value);
  return [...new Set(problems)].flatMap((raw) => {
    const value = text(raw),
      message = abilityMessage(raw);
    if (value === undefined || message === null) return [];
    const location = value.split(':')[0] ?? '';
    const relative = location.startsWith(prefix) ? location.slice(prefix.length) : '';
    const path: AbilityPath = /^(?:\.[\w-]+|\[\d+\])*$/.test(relative)
      ? [...relative.matchAll(/\.([\w-]+)|\[(\d+)\]/g)].map((part) => part[1] ?? Number(part[2]))
      : [];
    const current = nodeAt(e.node, path),
      submitted = nodeAt(sent, path);
    if (
      !viewEqual(
        current === undefined ? undefined : abilityValue(current),
        submitted === undefined ? undefined : abilityValue(submitted),
      )
    )
      return [];
    return [
      { message, path, fieldId: abilityFieldId(current?.kind === 'array' ? [...path, 0] : path) },
    ];
  });
}
export function abilityFieldProblems(problems: readonly AbilityProblem[], path: AbilityPath) {
  return problems.filter(
    (problem) =>
      problem.path.length > 0 &&
      (problem.path.every((part, index) => part === path[index]) ||
        path.every((part, index) => part === problem.path[index])),
  );
}
export function abilityErrorAttributes(problems: readonly AbilityProblem[], path: AbilityPath) {
  const id = abilityFieldId(path);
  return {
    id,
    'aria-invalid': abilityFieldProblems(problems, path).length > 0,
    'aria-describedby': `${id}-server-errors`,
  };
}
