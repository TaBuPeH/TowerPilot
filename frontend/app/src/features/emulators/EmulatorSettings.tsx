import { useState } from 'react';
import { copy } from '../../copy/catalogue.ts';
import { selectEmulatorSettings } from '../../store/selectors/emulatorSettings.ts';
import type { SettingsSaveCommand } from '../../store/selectors/settingsModule.ts';
import type { ServerText } from '../../copy/server.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow } from '../../ui/ActionRow.tsx';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Region, RegionBody, RegionEnd } from '../../ui/Region.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { useDashboard, useView } from '../dashboard/context.ts';
import { RevocationFallback } from '../dashboard/shared/RevocationFallback.tsx';
import { SettingsModuleForm } from '../settings/SettingsModuleForm.tsx';

const t = copy.emulatorSettings;

/** Saved defaults and the separately reviewed context change share the settings editor. */
export function EmulatorSettings() {
  const { commands } = useDashboard();
  const vm = useView(selectEmulatorSettings);
  const [review, setReview] = useState<{
    readonly command: SettingsSaveCommand;
    readonly label: ServerText<'name'>;
  } | null>(null);
  const bindings = {
    onEdit: commands.editDraft,
    onDiscard: commands.discardDraft,
    onDismiss: commands.dismiss,
  };
  return (
    <>
      <Region
        id="emulatorsContext"
        titleId="emulators-context-title"
        title={t.context.short}
        fallback={<RevocationFallback part="emulators" />}
      >
        {t.context.why === undefined ? null : (
          <RegionEnd>
            <HelpTip label={t.context.short}>
              <p>{t.context.why}</p>
            </HelpTip>
          </RegionEnd>
        )}
        <RegionBody>
          {vm.state !== 'stale' ? null : <p>{t.stale.short}</p>}
          {vm.submittedFrom === null ? null : (
            <Facts>
              <Fact
                label={t.submittedFrom.short}
                value={<ServerValue kind="id" value={vm.submittedFrom} />}
              />
            </Facts>
          )}
          {vm.active === null ? (
            <p>
              {
                (vm.state === 'ready'
                  ? t.noModule
                  : vm.state === 'not_read'
                    ? t.notRead
                    : t.unavailable
                ).short
              }
            </p>
          ) : (
            <SettingsModuleForm
              {...bindings}
              vm={vm.active}
              labelledBy="emulators-context-title"
              description={null}
              onSave={(command) => {
                if (vm.intended !== null) setReview({ command, label: vm.intended });
              }}
            />
          )}
        </RegionBody>
      </Region>
      <Region
        id="emulatorsDefaults"
        titleId="emulators-defaults-title"
        title={t.configured.short}
        fallback={<RevocationFallback part="emulators" />}
      >
        {t.configured.why === undefined ? null : (
          <RegionEnd>
            <HelpTip label={t.configured.short}>
              <p>{t.configured.why}</p>
            </HelpTip>
          </RegionEnd>
        )}
        <RegionBody>
          {vm.defaults === null ? (
            <p>
              {
                (vm.state === 'ready'
                  ? t.noModule
                  : vm.state === 'not_read'
                    ? t.notRead
                    : t.unavailable
                ).short
              }
            </p>
          ) : (
            <SettingsModuleForm
              {...bindings}
              vm={vm.defaults}
              labelledBy="emulators-defaults-title"
              description={null}
              onSave={(command) => {
                void commands.saveSettings(command);
              }}
            />
          )}
          <ActionRow>
            <Button
              tier="quiet"
              label={t.check.short}
              focusKey="emulators:defaults:read"
              onClick={commands.checkSettings}
            />
          </ActionRow>
        </RegionBody>
      </Region>
      <ConfirmDialog
        open={review !== null}
        title={
          <>
            {t.confirmBefore.short}
            {review === null ? null : <ServerValue kind="name" value={review.label} />}
            {t.confirmAfter.short}
          </>
        }
        body={t.confirmBody.short}
        confirmLabel={t.confirm.short}
        cancelLabel={t.cancel.short}
        tier="primary"
        onCancel={() => {
          setReview(null);
        }}
        onConfirm={() => {
          if (review !== null) void commands.saveSettings(review.command);
          setReview(null);
        }}
      />
    </>
  );
}
