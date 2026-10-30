import { configContext } from '../../core/config.ts';
import { settingsView } from '../../core/settingsFields.ts';
import { fromServer } from '../../copy/server.ts';
import { settledByRead } from '../fences.ts';
import { sameContextView } from '../intents.ts';
import { answeredBody } from '../reads.ts';
import type { DashboardState, IntentView } from '../types.ts';
import {
  saveOutcome,
  settingsModuleVM,
  settingsSavePending,
  type SettingsModuleVM,
  type SaveOutcomeVM,
} from './settingsModule.ts';

function switchRecord(state: DashboardState): IntentView | undefined {
  return state.intents.findLast(
    (record) => record.kind === 'settings_save' && record.meta.module === 'active-instance',
  );
}

/** The switch stays pending until config verifies the intended transition or a person accepts. */
export function emulatorSwitchPending(state: DashboardState): boolean {
  const record = switchRecord(state);
  return (
    record !== undefined &&
    !record.accepted &&
    record.phase !== 'refused' &&
    settledByRead(state, record) === null
  );
}

function switchOutcome(state: DashboardState): SaveOutcomeVM | null {
  const record = switchRecord(state);
  if (record === undefined || record.context === null) return null;
  const settled = settledByRead(state, record);
  const current = sameContextView(state.context, record.context);
  if (
    !current &&
    settled === 'applied' &&
    record.meta.values?.['active_instance'] !== state.context?.instance
  )
    return null;
  // The shared outcome retains the submitting target; only this transition may be
  // displayed after adopting the verified new context. Never rebind its journal.
  const vm = saveOutcome({ ...state, context: record.context }, record.target);
  if (vm === null || vm.code === 'sending' || vm.code === 'refused' || settled !== null) return vm;
  const read = state.reads.config.latest;
  const fresh =
    read !== null &&
    read.startedSeq > Math.max(record.sentSeq, record.answerSeq ?? record.lostSeq ?? 0) &&
    read.outcome.kind === 'answered' &&
    read.outcome.status === 200 &&
    configContext(read.outcome.body) !== null;
  return {
    ...vm,
    code: 'unknown',
    word: 'unknown',
    accepted: record.accepted === true,
    acceptOffered: record.accepted !== true && fresh,
    dismissOffered: record.accepted === true,
  };
}

export function selectEmulatorSettings(state: DashboardState) {
  const view = settingsView(answeredBody(state, 'settings'));
  const read = state.reads.settings.latest;
  const failed = read !== null && (read.outcome.kind !== 'answered' || read.outcome.status !== 200);
  const pending = settingsSavePending(state, ['settings']) || emulatorSwitchPending(state);
  const module = (id: string): SettingsModuleVM | null => {
    const vm = settingsModuleVM(state, view, id, { pending });
    return vm === null || !failed
      ? vm
      : { ...vm, save: { ...vm.save, enabled: false, reason: 'context_unknown', command: null } };
  };
  const active = module('active-instance');
  const choice = active?.save.command?.values['active_instance'];
  const field = active?.fields.find((field) => field.path === 'active_instance');
  const outcome = switchOutcome(state);
  return {
    submittedFrom:
      outcome === null ? null : fromServer('id', switchRecord(state)?.context?.instance),
    defaults: module('instance-defaults'),
    active: active === null ? null : { ...active, outcome },
    intended:
      typeof choice === 'string'
        ? (field?.options.find((option) => option.value === choice)?.label ??
          fromServer('name', choice))
        : null,
    state:
      view === null ? (read === null ? 'not_read' : 'unavailable') : failed ? 'stale' : 'ready',
  };
}
