import { settingsModule, settingsView } from '../../core/settingsFields.ts';
import { fromServer } from '../../copy/server.ts';
import { answeredBody } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { settingsMatchContext, settingsModuleVM, settingsSavePending } from './settingsModule.ts';

export function selectRunDefaults(state: DashboardState) {
  const view = settingsView(answeredBody(state, 'settings'));
  const read = state.reads.settings;
  const preset = settingsMatchContext(view, state.context)
    ? settingsModule(view, 'instance-defaults')?.fields.find((field) => field.path === 'preset')
    : undefined;
  const status: 'not_read' | 'unavailable' | 'other_context' | 'stale' | null =
    view === null
      ? read.latest === null
        ? 'not_read'
        : 'unavailable'
      : !settingsMatchContext(view, state.context)
        ? 'other_context'
        : read.latest?.outcome.kind === 'failed'
          ? 'stale'
          : null;
  return {
    instancePreset: fromServer(
      'name',
      preset?.options.find((option) => option.value === preset.value)?.label ?? preset?.value,
    ),
    vm: settingsModuleVM(state, view, 'run-defaults', {
      pending: settingsSavePending(state, ['settings']),
    }),
    status,
  };
}
