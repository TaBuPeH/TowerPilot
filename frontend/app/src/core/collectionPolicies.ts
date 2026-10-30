import { entries, id, list, own, ownPath, record, stringList, text } from './read.ts';

/**
 * The Collection page's reading of the active profile (collection brief C4 to C6): the
 * gathering policies (`policies.gather`), which runs use each one
 * (`blueprints.<run>.policies.gather`), and the between-run chores (`policies.chores`,
 * an opt-out list). Saved choices only: nothing here says a reward was collected, a
 * quota is left, or a chore ran.
 */

export interface GatherPolicy {
  readonly id: string;
  /** The policy's own label, or null (then its id names it). */
  readonly label: string | null;
  /** The runs (blueprint keys) whose saved settings name this policy. */
  readonly usedBy: readonly string[];
  /** The policy's whole saved body, unknown fields included (a copy keeps them). */
  readonly body: unknown;
}

export interface CollectionSource {
  readonly profile: string | null;
  readonly revision: string | null;
  readonly policies: readonly GatherPolicy[];
  /** Runs naming a policy the profile does not define (a problem to show, never fixed here). */
  readonly missing: readonly { readonly run: string; readonly policy: string }[];
  readonly chores: readonly { readonly name: string; readonly enabled: boolean }[] | null;
  readonly problems: readonly string[];
  readonly warnings: readonly string[];
  readonly validatorError: string | null;
}

/** A readable profile source answer, else null. */
export function collectionSource(body: unknown): CollectionSource | null {
  const profile = own(body, 'profile');
  if (record(profile) === undefined) return null;
  const gather = ownPath(profile, 'policies', 'gather');
  const runs = entries(own(profile, 'blueprints')).flatMap(([run, blueprint]) => {
    const policy = id(ownPath(blueprint, 'policies', 'gather'));
    return policy === undefined ? [] : [{ run, policy }];
  });
  const ids = entries(gather).map(([policyId]) => policyId);
  const chores = list(ownPath(profile, 'policies', 'chores'));
  return {
    profile: id(own(body, 'name')) ?? null,
    revision: id(own(body, 'revision')) ?? null,
    policies: entries(gather).map(([policyId, policy]) => ({
      id: policyId,
      label: text(own(policy, 'label')) ?? null,
      usedBy: runs.filter((entry) => entry.policy === policyId).map((entry) => entry.run),
      body: policy,
    })),
    missing: runs.filter((entry) => !ids.includes(entry.policy)),
    chores:
      chores === undefined
        ? null
        : chores.flatMap((entry) => {
            const name = id(own(entry, 'name'));
            return name === undefined ? [] : [{ name, enabled: own(entry, 'enabled') !== false }];
          }),
    problems: stringList(own(body, 'problems')) ?? [],
    warnings: stringList(own(body, 'warnings')) ?? [],
    validatorError: text(own(body, 'validator_error')) ?? null,
  };
}

/** The chore names the vocabulary lists (`vocab.chore_names.values`), in priority order. */
export function choreNames(vocabBody: unknown): readonly string[] | null {
  return stringList(ownPath(vocabBody, 'vocab', 'chore_names', 'values')) ?? null;
}

export interface ChoreRow {
  readonly name: string;
  /** Saved: listed with `enabled: false` is off; listed on or not listed is on (opt-out list). */
  readonly enabled: boolean;
  /** Listed in the profile but not in the vocabulary: kept as saved, never edited here. */
  readonly unknown: boolean;
}

export function choreRows(
  source: CollectionSource | null,
  names: readonly string[] | null,
): readonly ChoreRow[] {
  const listed = source?.chores ?? [];
  const known = names ?? [];
  return [
    ...known.map((name) => ({
      name,
      enabled: listed.find((entry) => entry.name === name)?.enabled ?? true,
      unknown: false,
    })),
    ...listed
      .filter((entry) => !known.includes(entry.name))
      .map((entry) => ({ ...entry, unknown: true })),
  ];
}

/**
 * The whole chores list a save writes (`policies.chores`): every saved entry in its order
 * with the person's switches applied, then each changed chore not listed yet. Entries the
 * vocabulary does not know stay exactly as saved.
 */
export function choresValue(
  source: CollectionSource | null,
  switches: Readonly<Record<string, boolean>>,
): readonly { readonly name: string; readonly enabled: boolean }[] {
  const listed = source?.chores ?? [];
  const kept = listed.map((entry) => ({
    name: entry.name,
    enabled: switches[entry.name] ?? entry.enabled,
  }));
  const added = Object.entries(switches)
    .filter(([name, enabled]) => !listed.some((entry) => entry.name === name) && !enabled)
    .map(([name, enabled]) => ({ name, enabled }));
  return [...kept, ...added];
}

/** A policy identifier as the previous page accepted it: lowercase, digits, `_`, a letter first. */
const POLICY_ID = /^[a-z][a-z0-9_]*$/u;

export type PolicyIdCheck = 'enter_id' | 'bad_id' | 'id_taken' | null;

export function policyIdCheck(
  raw: string,
  source: CollectionSource,
  settingsBody?: unknown,
): PolicyIdCheck {
  const trimmed = raw.trim();
  if (trimmed === '') return 'enter_id';
  if (!POLICY_ID.test(trimmed)) return 'bad_id';
  // A taken name replaces a policy at the patch endpoint. A settings read may
  // list a policy not yet present in the source read; never offer that name.
  const modules = Object.values(policyModules(trimmed));
  const listed =
    (list(own(settingsBody, 'modules')) ?? []).some((module) =>
      modules.some((name) => own(module, 'id') === name),
    ) ||
    (list(own(settingsBody, 'policies')) ?? []).some(
      (policy) => own(policy, 'section') === 'gather' && own(policy, 'id') === trimmed,
    );
  return source.policies.some((policy) => policy.id === trimmed) || listed ? 'id_taken' : null;
}

/**
 * A copy's body: the source policy's whole saved body, unknown fields included, with the
 * new label (none: the source's label is dropped, so the id names the copy).
 */
export function copiedPolicy(
  source: GatherPolicy,
  label: string,
): Readonly<Record<string, unknown>> {
  const fields = Object.fromEntries(entries(source.body).filter(([key]) => key !== 'label'));
  const trimmed = label.trim();
  return trimmed === '' ? fields : { ...fields, label: trimmed };
}

/** The settings module ids of one gathering policy (`GET /api/settings`). */
export function policyModules(policyId: string): {
  readonly gather: string;
  readonly ad: string;
  readonly orbit: string;
} {
  return { gather: `gather:${policyId}`, ad: `ad:${policyId}`, orbit: `orbit:${policyId}` };
}

/** The settings module of the profile-wide rewards (C1). */
export const REWARDS_MODULE = 'rewards';
