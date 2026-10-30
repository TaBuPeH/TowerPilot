import { useState } from 'react';
import { editingTarget as t } from '../../copy/editingTarget.ts';
import { editingFilesFailed, selectEditingChoices } from '../../store/selectors/editingTarget.ts';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { Button } from '../../ui/Button.tsx';
import { ServerOption } from '../../ui/ServerValue.tsx';
import { useDashboard, useView } from '../dashboard/context.ts';
import { EditingTargetLine } from './EditingTargetLine.tsx';

export function EditingTargetPicker() {
  const { commands } = useDashboard();
  const epoch = useView((state) => state.epoch);
  const choices = useView(selectEditingChoices);
  const failed = useView(editingFilesFailed);
  const [pick, setPick] = useState('');
  const available = choices.some((choice) => choice.key === pick);
  return (
    <div data-form="editing-target">
      <Tile
        as="fieldset"
        className="tp-profiles__form"
        titleId="profiles-editing-target-title"
        title={t.title.short}
      >
        <EditingTargetLine inPicker />
        <div>{t.scope.short}</div>
        {failed ? (
          <div role="alert">
            {t.listFailed.short}
            <Button
              tier="secondary"
              label={t.retryList.short}
              onClick={commands.checkEditingProfiles}
            />
          </div>
        ) : null}
        <div className="tp-control-row">
          <label className="tp-profiles__field">
            <span>{t.picker.short}</span>
            <select
              className="tp-field-control"
              value={available ? pick : ''}
              onChange={(event) => {
                setPick(event.currentTarget.value);
              }}
            >
              <option value="">{t.choose.short}</option>
              {choices.map((choice) =>
                choice.name === null ? null : (
                  <ServerOption key={choice.key} value={choice.key} label={choice.name} />
                ),
              )}
            </select>
          </label>
          <ActionRow
            reason={
              available ? undefined : <Reason id="editing-target-pick-reason" entry={t.choose} />
            }
          >
            {available ? (
              <Button
                tier="secondary"
                label={t.pick.short}
                onClick={() => {
                  commands.pickEditingTarget({ epoch, name: pick });
                }}
              />
            ) : (
              <Button
                tier="secondary"
                label={t.pick.short}
                disabled
                reasonId="editing-target-pick-reason"
                onClick={() => undefined}
              />
            )}
            <Button
              tier="quiet"
              label={t.return.short}
              onClick={() => {
                commands.returnEditingToActive({ epoch });
              }}
            />
          </ActionRow>
        </div>
      </Tile>
    </div>
  );
}
