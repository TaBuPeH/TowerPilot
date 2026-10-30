import { entries, list, own, ownPath, text } from '../../core/read.ts';
import { settingsView } from '../../core/settingsFields.ts';
import { fromServer } from '../../copy/server.ts';
import { answeredBody } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { settingsModuleVM, settingsSavePending } from './settingsModule.ts';
import {
  canSaveEquipment,
  currentEquipment,
  currentLoadout,
  equipmentPending,
  type EquipmentState,
} from '../equipment.ts';
import {
  configRevision,
  discoveredOptions,
  equipmentBody,
  equipmentDraft,
  equipmentMode,
  EQUIPMENT_FIELDS,
  invalidEquipment,
  loadoutOf,
  restoreEligibility,
  restoreUsage,
  restoreInvalid,
  restoreModuleRows,
  restoreModuleValue,
  restoreRowInvalid,
} from '../equipmentDraft.ts';

export function selectTournamentSettings(state: DashboardState) {
  const failed = state.reads.settings.latest?.outcome.kind === 'failed';
  return {
    module: settingsModuleVM(
      state,
      settingsView(answeredBody(state, 'settings')),
      'tournament-cards',
      { pending: failed || settingsSavePending(state, ['settings']) },
    ),
    account: fromServer('name', state.context?.account),
    failed,
  };
}

const messages = (value: unknown) =>
  (list(value) ?? []).flatMap((line) => {
    const shown = fromServer('message', line);
    return shown === null ? [] : [shown];
  });
export function selectEquipment(s: EquipmentState, now: number) {
  const target = currentEquipment(s);
  const e = currentLoadout(s);
  const names = Object.keys(own(target?.config, 'loadouts') ?? {}).sort();
  const selected = target?.selected ?? null;
  const draftBody = e === null ? undefined : equipmentBody(e.draft, e.original);
  const invalid = e === null ? [] : invalidEquipment(e.draft, target?.profile);
  const blueprints = entries(ownPath(target?.profile, 'profile', 'blueprints'));
  const consumers = (name: string) =>
    blueprints.flatMap(([key, bp]) => {
      const label = fromServer('name', own(bp, 'label')) ?? fromServer('name', key);
      return own(bp, 'loadout') !== name || label === null ? [] : [{ key, label }];
    });
  const rows = [...new Set([...names, ...(selected === null ? [] : [selected])])]
    .sort()
    .flatMap((name) => {
      const label = fromServer('name', name);
      if (label === null) return [];
      const original = loadoutOf(target?.config, name);
      const d = equipmentDraft(original);
      return [
        {
          key: name,
          label,
          summary:
            own(original, 'defined') === false
              ? ('placeholder' as const)
              : entries(original).length === 0
                ? ('untouched' as const)
                : equipmentMode(original),
          selections: EQUIPMENT_FIELDS.flatMap((field) => {
            const value = fromServer('name', own(original, field));
            return value === null ? [] : [{ field, value }];
          }),
          consumers: consumers(name),
          missing: invalidEquipment(d, target?.profile).length > 0,
          dirty: target?.editors[name]?.dirty === true,
        },
      ];
    });
  const before = fromServer('detail', JSON.stringify(e?.original ?? null, null, 2));
  const latest = fromServer(
    'detail',
    JSON.stringify(
      selected === null ? null : (loadoutOf(target?.config, selected) ?? null),
      null,
      2,
    ),
  );
  const after = fromServer('detail', JSON.stringify(draftBody ?? null, null, 2));
  const revision = configRevision(target?.config);
  const restoreCards = e?.draft.restoreCards ?? text(own(e?.original, 'cards_restore')) ?? '';
  const restoreChoices = discoveredOptions(target?.profile, 'cards');
  return {
    restore: {
      cards: restoreCards,
      missing:
        restoreCards !== '' && !restoreChoices.includes(restoreCards)
          ? fromServer('name', restoreCards)
          : null,
      choices: restoreChoices.flatMap((value) => {
        const label = fromServer('name', value);
        return label === null ? [] : [{ value, label }];
      }),
      rows:
        e === null
          ? []
          : restoreModuleRows(e.draft, e.original).map((row) => ({
              ...row,
              name: fromServer('name', row.module),
              invalid: restoreRowInvalid(row),
              raw: restoreRowInvalid(row)
                ? fromServer('detail', JSON.stringify(restoreModuleValue(row)))
                : null,
            })),
      invalid: e !== null && restoreInvalid(e.draft),
      usage:
        selected === null
          ? null
          : restoreUsage(target?.profile, selected, s.context?.profile ?? null),
      blocked:
        e === null || selected === null || e.revision !== revision
          ? null
          : restoreEligibility(
              e.draft,
              e.original,
              target?.profile,
              selected,
              s.context?.profile ?? null,
            ),
    },
    target,
    entry: e,
    selected,
    rows,
    consumers: selected === null ? [] : consumers(selected),
    name: fromServer('name', selected),
    instance: fromServer('name', s.context?.instance),
    account: fromServer('name', s.context?.account),
    profile: fromServer('name', s.context?.profile),
    revision: fromServer('id', revision),
    base: fromServer('id', e?.revision),
    before,
    latest,
    after,
    missingTarget: selected !== null && !names.includes(selected),
    stale: target?.readAt != null && now - target.readAt > 30_000,
    canSave: canSaveEquipment(s, now),
    pending: e !== null && equipmentPending(e),
    refusalContext:
      e?.outcome === 'contextChanged' && e.refusalContext !== undefined
        ? fromServer('detail', JSON.stringify(e.refusalContext, null, 2))
        : null,
    emptyBody: entries(draftBody).length === 0,
    manual: e !== null && equipmentMode(e.original) === 'manual',
    conversion: e !== null && equipmentMode(e.original) === 'manual' && e.draft.mode !== 'manual',
    conversionReviewed: e !== null && e.reviewedVersion === e.version,
    removed: entries(e?.original).flatMap(([key]) =>
      own(draftBody, key) === undefined ? [fromServer('name', key)].filter((v) => v !== null) : [],
    ),
    reconcile: e !== null && (e.reconcile || (e.dirty && e.revision !== revision)),
    fields: EQUIPMENT_FIELDS.filter((field) =>
      e?.draft.mode === 'global'
        ? field === 'global_preset'
        : e?.draft.mode === 'category' && field !== 'global_preset',
    ).map((field) => ({
      key: field,
      value: e?.draft.values[field] ?? '',
      invalid: invalid.includes(field),
      missingOption: invalid.includes(field) ? fromServer('name', e?.draft.values[field]) : null,
      options: discoveredOptions(target?.profile, field).flatMap((value) => {
        const label = fromServer('name', value);
        return label === null ? [] : [{ value, label }];
      }),
    })),
    errors: messages([
      own(e?.intent?.answer?.body, 'error'),
      ...(list(own(e?.intent?.answer?.body, 'problems')) ?? []),
    ]),
    warnings: messages([
      ...(list(own(target?.profile, 'warnings')) ?? []),
      ...(list(own(target?.profile, 'problems')) ?? []),
      own(target?.profile, 'validator_error'),
      ...(list(own(e?.intent?.answer?.body, 'warnings')) ?? []),
    ]),
    validationFromEarlierRead: target?.profileConfigRevision !== revision,
    resultRevision: fromServer(
      'id',
      configRevision(e?.intent?.answer?.body) ??
        (e?.intent?.readSettlement == null ? null : revision),
    ),
  };
}
