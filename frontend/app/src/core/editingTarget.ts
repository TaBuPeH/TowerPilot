import { type ProfileFile, type ProfileFilesView } from './profiles.ts';

/** F6-14 D1-D2: identity excludes the active profile; pins survive activation changes. */
export interface EditingContext {
  readonly instance: string;
  readonly serial: string;
  readonly account: string | null;
  readonly profile: string | null;
}

export interface EditTargetPick {
  readonly name: string;
  readonly generation: number;
}

export type EditTargets = Readonly<Record<string, EditTargetPick>>;
export type EditingTargetBlock = 'no_profile' | 'files_unread' | 'not_listed' | 'starter' | 'draft';

export interface EditingTarget {
  readonly name: string | null;
  readonly active: string | null;
  readonly pinned: boolean;
  readonly inactive: boolean;
  readonly generation: number;
  readonly blocked: EditingTargetBlock | null;
}

export function editTargetKey(context: EditingContext): string {
  return JSON.stringify([context.instance, context.serial, context.account]);
}

/** D7: a draft belongs to this runtime identity and edited file, never the active binding. */
export function editTargetScope(context: EditingContext, name: string): string {
  return JSON.stringify([context.instance, context.serial, context.account, name]);
}

export function pickableProfiles(files: ProfileFilesView | null): readonly ProfileFile[] {
  return files?.files.filter((file) => !file.draft && !file.starter) ?? [];
}

export function editingTarget(
  context: EditingContext | null,
  pick: EditTargetPick | undefined,
  files: ProfileFilesView | null,
): EditingTarget {
  const active = context?.profile ?? null;
  const name = pick?.name ?? active;
  const pinned = pick !== undefined;
  const file = files?.files.find((entry) => entry.name === name);
  const blocked =
    name === null
      ? 'no_profile'
      : !pinned
        ? null
        : files === null
          ? 'files_unread'
          : file === undefined
            ? 'not_listed'
            : file.starter
              ? 'starter'
              : file.draft
                ? 'draft'
                : null;
  return {
    name,
    active,
    pinned,
    inactive: name !== active,
    generation: pick?.generation ?? 0,
    blocked,
  };
}
