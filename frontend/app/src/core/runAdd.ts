import { entries, id, list, own, ownPath, record, text } from './read.ts';
import type { DraftValues } from './runControls.ts';

/** RL4 recipe creation rules. The recipe catalogue owns the offered defaults and policies. */
export interface RunRecipe {
  readonly id: string;
  readonly label: string;
  readonly kind: string;
  readonly defaults: unknown;
  readonly fields: readonly string[];
}

export function runRecipes(body: unknown): readonly RunRecipe[] {
  return (list(own(body, 'templates')) ?? []).flatMap((item) => {
    const key = id(own(item, 'id'));
    const label = text(own(item, 'label'));
    const defaults = own(item, 'blueprint');
    const kind = id(own(defaults, 'kind'));
    if (key === undefined || label === undefined || kind === undefined) return [];
    return [
      {
        id: key,
        label,
        kind,
        defaults,
        fields: [
          'label',
          'tier',
          'loadout',
          ...(kind === 'shard' ? ['count'] : []),
          ...(kind === 'tournament' ? ['gem_entry_max'] : []),
          ...(own(defaults, 'dissonant_tab') === undefined ? [] : ['dissonant_tab', 'perk_bans']),
          ...['uw', 'rescue']
            .filter((policy) => id(ownPath(item, 'optional_policies', policy)) !== undefined)
            .map((policy) => `enable_${policy}`),
        ],
      },
    ];
  });
}

export function runAddLoadouts(config: unknown): readonly string[] {
  return entries(own(config, 'loadouts')).map(([key]) => key);
}

export function runAddSource(body: unknown, profile: string): boolean {
  return (
    own(body, 'name') === profile &&
    record(own(body, 'profile')) !== undefined &&
    own(body, 'ok') !== false
  );
}

export function runIdCheck(
  raw: string,
  source: unknown,
): 'enter_id' | 'bad_id' | 'id_taken' | null {
  if (raw === '') return 'enter_id';
  if (!/^[a-z][a-z0-9_]{0,63}$/u.test(raw)) return 'bad_id';
  return ownPath(source, 'profile', 'blueprints', raw) === undefined ? null : 'id_taken';
}

/** Raw fields stay untouched. Only explicitly chosen options are sent; label is always required. */
export function runAddOptions(
  recipe: RunRecipe,
  draft: DraftValues,
): Readonly<Record<string, unknown>> | null {
  if (typeof draft['label'] !== 'string' || draft['label'].trim() === '') return null;
  const options: Record<string, unknown> = { label: draft['label'] };
  for (const field of recipe.fields) {
    const value = draft[field];
    if (field === 'label' || value === undefined) continue;
    if (value === '' && field !== 'tier' && field !== 'count' && field !== 'gem_entry_max')
      continue;
    if (field.startsWith('enable_')) {
      if (typeof value !== 'boolean') return null;
      options[field] = value;
    } else if (field === 'tier' || field === 'count' || field === 'gem_entry_max') {
      if (typeof value !== 'string' || value.trim() === '' || !Number.isSafeInteger(Number(value)))
        return null;
      options[field] = Number(value);
    } else if (field === 'perk_bans') {
      if (typeof value !== 'string') return null;
      options[field] = value.split('\n');
    } else {
      options[field] = value;
    }
  }
  // These recipes' shipped loadout names may not exist on this account. Require an explicit choice.
  if (
    (recipe.kind !== 'coin' || recipe.fields.includes('dissonant_tab')) &&
    typeof options['loadout'] !== 'string'
  )
    return null;
  return options;
}

export interface RunAddEvidence {
  readonly blueprint?: string;
  readonly preset?: string;
  readonly baseRevision?: string;
}

/** RL4: only stable pre-write codes at their recorded HTTP status prove refusal. */
export function runAddRefusal(answer: { readonly status: number; readonly body: unknown }) {
  if (own(answer.body, 'ok') !== false) return null;
  const code = own(answer.body, 'code');
  if (answer.status === 400) {
    switch (code) {
      case 'invalid_request':
      case 'id_taken':
      case 'profile_unavailable':
      case 'recipe_refused':
      case 'validation_failed':
      case 'write_failed_nothing_saved':
        return code;
    }
  }
  if (answer.status === 409) {
    switch (code) {
      case 'revision_conflict':
      case 'context_changed':
      case 'profile_unavailable':
        return code;
    }
  }
  return null;
}

function runAddPresent(body: unknown, sent: RunAddEvidence): boolean {
  return (
    sent.blueprint !== undefined &&
    sent.preset !== undefined &&
    ownPath(body, 'profile', 'blueprints', sent.blueprint, 'kind') === sent.preset
  );
}

/** A later read needs an advanced revision and the submitted run/kind; absence proves nothing. */
export function runAddRecorded(body: unknown, sent: RunAddEvidence): boolean {
  const revision = id(own(body, 'revision'));
  return (
    sent.baseRevision !== undefined &&
    sent.baseRevision.trim() !== '' &&
    revision !== undefined &&
    revision.trim() !== '' &&
    revision !== sent.baseRevision &&
    runAddPresent(body, sent)
  );
}

export function runAddAnswer(
  answer: { readonly status: number; readonly body: unknown },
  sent: RunAddEvidence,
): 'confirmed' | 'refused' | 'unconfirmed' {
  if (
    answer.status === 200 &&
    own(answer.body, 'ok') === true &&
    runAddPresent(answer.body, sent) &&
    (sent.baseRevision === undefined || id(own(answer.body, 'revision')) !== undefined)
  )
    return 'confirmed';
  // Only these stable codes guarantee a pre-write refusal. An uncoded 400 can come
  // from backup cleanup or the revision read AFTER saving; error text proves nothing.
  return runAddRefusal(answer) === null ? 'unconfirmed' : 'refused';
}
