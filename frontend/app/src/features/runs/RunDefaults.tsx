import { runtimeConfig as t } from '../../copy/runtimeConfig.ts';
import { runtime } from '../../copy/runtime.ts';
import { selectRunDefaults } from '../../store/selectors/runDefaults.ts';
import { useDashboard, useView } from '../dashboard/context.ts';
import { ScopedSettingsModule } from '../settings/ScopedSettingsModule.tsx';
import { Button } from '../../ui/Button.tsx';
import { ActionRow } from '../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { ValidationSummary } from './ValidationSummary.tsx';
import './runDefaults.css';

export function RunDefaults() {
  const { vm, status, instancePreset } = useView(selectRunDefaults);
  const { commands } = useDashboard();
  return (
    <div className="tp-run-defaults" data-run-defaults="true">
      <Disclosure
        label={t.fallback.short}
        preview={
          <ValidationSummary
            problems={vm?.fields.filter((field) => field.invalid !== null).length ?? 0}
          />
        }
      >
        {instancePreset === null ? null : (
          <Facts>
            <Fact
              label={t.instancePreset.short}
              value={<ServerValue kind="name" value={instancePreset} />}
            />
          </Facts>
        )}
        {status === null ? null : <p>{runtime.settingsStates[status].short}</p>}
        {vm === null ? (
          status === null ? (
            <p>{runtime.settingsStates.no_module.short}</p>
          ) : null
        ) : (
          <ScopedSettingsModule
            vm={vm}
            legend={t.modules['run-defaults']}
            intro={t.fallbackNotice}
          />
        )}
        <ActionRow>
          <Button
            tier="quiet"
            label={runtime.checkSettings.short}
            onClick={commands.checkSettings}
          />
        </ActionRow>
      </Disclosure>
    </div>
  );
}
