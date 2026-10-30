import { useId } from 'react';
import { copy } from '../../copy/catalogue.ts';
import { selectRunsAdd } from '../../store/selectors/runsAdd.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { CopyLine } from '../../ui/CopyLine.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { useDashboard, useView } from '../dashboard/context.ts';
import './addRun.css';

const t = copy.runsAdd;

/** RL4 displays the selector's choices. Core owns allowed options and save eligibility. */
export function AddRun() {
  const vm = useView(selectRunsAdd);
  const { commands } = useDashboard();
  const reasonId = useId();
  const edit = (path: string, value: string | boolean) => {
    if (vm.draft['base_revision'] === undefined && vm.baseRevision !== null)
      commands.editDraft(vm.scope, 'base_revision', vm.baseRevision);
    commands.editDraft(vm.scope, path, value);
  };
  return (
    <div className="tp-run-add" data-run-add="true">
      <Disclosure label={t.title.short} focusKey="run-add:open">
        <CopyLine entry={t.scope} variant="intro" />
        <div className="tp-run-add__fields">
          <label>
            <span id={`${reasonId}-recipe`}>{t.recipe.short}</span>
            <select
              className="tp-field-control"
              aria-labelledby={`${reasonId}-recipe`}
              value={vm.selected}
              onChange={(event) => {
                edit('template', event.currentTarget.value);
              }}
            >
              {vm.recipes.map((recipe) =>
                recipe.label === null ? null : (
                  <ServerOption key={recipe.id} value={recipe.id} label={recipe.label} />
                ),
              )}
            </select>
          </label>
          <label>
            <span>{t.runId.short}</span>
            <input
              className="tp-field-control"
              value={vm.runId}
              onChange={(event) => {
                edit('run_id', event.currentTarget.value);
              }}
            />
          </label>
          {Object.entries(t.fields)
            .filter(([field]) => vm.fields.includes(field))
            .map(([field, label]) => {
              const value = vm.draft[field];
              return (
                <label key={field}>
                  <span id={`${reasonId}-${field}`}>{label.short}</span>
                  {field.startsWith('enable_') ? (
                    <input
                      type="checkbox"
                      checked={value === true}
                      onChange={(event) => {
                        edit(field, event.currentTarget.checked);
                      }}
                    />
                  ) : field === 'loadout' ? (
                    <select
                      className="tp-field-control"
                      aria-labelledby={`${reasonId}-${field}`}
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => {
                        edit(field, event.currentTarget.value);
                      }}
                    >
                      <option value="">{vm.asIs ? t.defaults.short : t.choose.short}</option>
                      {vm.asIs ? <option value="as_is">{t.asIs.short}</option> : null}
                      {vm.loadouts.map((loadout) =>
                        loadout.label === null ? null : (
                          <ServerOption key={loadout.id} value={loadout.id} label={loadout.label} />
                        ),
                      )}
                    </select>
                  ) : field === 'dissonant_tab' ? (
                    <select
                      className="tp-field-control"
                      aria-labelledby={`${reasonId}-${field}`}
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => {
                        edit(field, event.currentTarget.value);
                      }}
                    >
                      <option value="">{t.defaults.short}</option>
                      {Object.entries(t.tabs).map(([key, tab]) => (
                        <option key={key} value={key}>
                          {tab.short}
                        </option>
                      ))}
                    </select>
                  ) : field === 'perk_bans' ? (
                    <textarea
                      className="tp-field-control"
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => {
                        edit(field, event.currentTarget.value);
                      }}
                    />
                  ) : (
                    <input
                      className="tp-field-control"
                      inputMode={field === 'label' ? 'text' : 'numeric'}
                      value={typeof value === 'string' ? value : ''}
                      onChange={(event) => {
                        edit(field, event.currentTarget.value);
                      }}
                    />
                  )}
                </label>
              );
            })}
        </div>
        <div className="tp-run-add__actions">
          <ActionRow
            reason={
              vm.reason === null ? null : <Reason id={reasonId} entry={t.reasons[vm.reason]} />
            }
          >
            <Button
              tier="secondary"
              label={t.save.short}
              {...(vm.command === null ? { disabled: true, reasonId } : {})}
              onClick={() => {
                if (vm.command !== null) void commands.addRun(vm.command);
              }}
            />
            <Button tier="quiet" label={t.check.short} onClick={commands.checkRunAdd} />
            <Button
              tier="quiet"
              label={t.discard.short}
              onClick={() => {
                commands.discardDraft(vm.scope);
              }}
            />
          </ActionRow>
        </div>
        {vm.outcome === null ? null : (
          <div role="status" data-run-add-outcome={vm.outcome.phase}>
            <p>
              {vm.outcome.notAddedAgain
                ? t.notAddedAgain.short
                : t.outcomes[vm.outcome.phase].short}
            </p>
            {vm.outcome.refusal === null ? null : <p>{t.refusals[vm.outcome.refusal].short}</p>}
            {vm.outcome.error === null ? null : (
              <ServerValue kind="message" value={vm.outcome.error} />
            )}
            {vm.outcome.currentContext === null ? null : (
              <ServerValue kind="detail" value={vm.outcome.currentContext} />
            )}
            {vm.outcome.accepted ? <p>{t.accepted.short}</p> : null}
            {vm.outcome.accept ? (
              <Button
                tier="quiet"
                label={t.accept.short}
                onClick={() => {
                  if (vm.outcome !== null) commands.acceptCurrent(vm.outcome.id);
                }}
              />
            ) : null}
            {vm.outcome.profiles ? <a href="#profiles">{t.profiles.short}</a> : null}
          </div>
        )}
        {vm.earlierUnknown.map((earlier) => (
          <div key={earlier.id} data-earlier-add="unknown">
            <p>{t.outcomes.unconfirmed.short}</p>
            {earlier.run === null ? null : <ServerValue kind="id" value={earlier.run} />}
          </div>
        ))}
      </Disclosure>
    </div>
  );
}
