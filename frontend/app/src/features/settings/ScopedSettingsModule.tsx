import type { CopyEntry } from '../../copy/text.ts';
import { useDashboard } from '../dashboard/context.ts';
import { SettingsModuleForm } from './SettingsModuleForm.tsx';
import type { SettingsModuleVM } from '../../store/selectors/settingsModule.ts';

/** Keep the target beside both the form legend and its newest save result. */
export function ScopedSettingsModule({
  vm,
  legend,
  intro,
  description,
  applyNotice,
}: {
  readonly vm: SettingsModuleVM;
  readonly legend: CopyEntry;
  readonly intro?: CopyEntry;
  readonly description?: CopyEntry | null;
  readonly applyNotice?: CopyEntry | null;
}) {
  const { commands } = useDashboard();
  return (
    <div>
      <SettingsModuleForm
        vm={vm}
        legend={legend}
        {...(intro === undefined ? {} : { intro })}
        {...(description === undefined ? {} : { description })}
        {...(applyNotice === undefined ? {} : { applyNotice })}
        onEdit={commands.editDraft}
        onDiscard={commands.discardDraft}
        onSave={(command) => {
          void commands.saveSettings(command);
        }}
        onDismiss={commands.dismiss}
      />
      {vm.outcome === null ? null : (
        <div data-settings-outcome-scope={vm.id}>
          <span>{legend.short}</span>
          {intro === undefined ? null : <span>{intro.short}</span>}
        </div>
      )}
    </div>
  );
}
