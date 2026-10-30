import { useId, useLayoutEffect, useState } from 'react';
import { copy } from '../../copy/catalogue.ts';
import type { CopyEntry } from '../../copy/text.ts';
import { pinLibrary } from '../../core/shoppingLibrary.ts';
import {
  draftChanges,
  includeUpgrade,
  leaveOutUpgrade,
  moveUpgrade,
  setExcluded,
  stagePlan,
  type QueueBase,
  type QueueDraft,
} from '../../core/shoppingPlan.ts';
import {
  selectShoppingLibrary,
  type LibraryReason,
  type ShoppingLibraryPolicyVM,
} from '../../store/selectors/shoppingLibrary.ts';
import type { ProfilePatchCommand } from '../../store/selectors/collection.ts';
import type { QueueEditorVM } from '../../store/selectors/shoppingQueue.ts';
import { selectQueueEditor } from '../../store/selectors/shoppingQueue.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Region, RegionBody } from '../../ui/Region.tsx';
import { ShownList } from '../../ui/ShownList.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { pageHref } from '../../ui/routes.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useDashboard, useView } from '../dashboard/context.ts';
import { RevocationFallback } from '../dashboard/shared/RevocationFallback.tsx';
import { SaveOutcome } from '../settings/SaveOutcome.tsx';
import { QueueEditor } from '../shopping/QueueEditor.tsx';
import { LegacyEditor } from './ShoppingLibraryLegacy.tsx';
import { ValidationSummary } from './ValidationSummary.tsx';
import './shoppingLibrary.css';

const t = copy.shoppingLibrary;

/** The owning selectors already counted these problems for each editor. */
function policyProblems(policy: ShoppingLibraryPolicyVM): number {
  return policy.base.plan === null ? (policy.legacy?.problems ?? 0) : policy.queue.review.problems;
}

function Action({
  label,
  reason,
  onClick,
  id,
}: {
  readonly label: CopyEntry;
  readonly reason: LibraryReason;
  readonly onClick: () => void;
  readonly id?: string;
}) {
  const own = useId();
  const unavailable = reason === null ? null : t.reasons[reason];
  return (
    <ActionRow reason={unavailable === null ? null : <Reason id={own} entry={unavailable} />}>
      {unavailable === null ? (
        <Button
          tier="secondary"
          label={label.short}
          onClick={onClick}
          {...(id === undefined ? {} : { id })}
        />
      ) : (
        <Button
          tier="secondary"
          label={label.short}
          disabled
          reasonId={own}
          onClick={onClick}
          {...(id === undefined ? {} : { id })}
        />
      )}
    </ActionRow>
  );
}

/** Interaction adapter only: queue arithmetic and raw representation stay in the core owner. */
function LibraryQueue({
  base,
  draft,
  vm,
  onDraft,
}: {
  readonly base: QueueBase;
  readonly draft: QueueDraft;
  readonly vm: QueueEditorVM;
  readonly onDraft: (next: QueueDraft) => void;
}) {
  const own = useId();
  return (
    <QueueEditor
      idBase={own}
      vm={vm}
      onEdit={(key, value) => {
        onDraft({ ...draft, [key]: value });
      }}
      onInclude={(upgrade) => {
        onDraft(includeUpgrade(base, draft, upgrade));
      }}
      onLeaveOut={(upgrade) => {
        onDraft(leaveOutUpgrade(draft, upgrade));
      }}
      onExclude={(upgrade, excluded) => {
        onDraft(setExcluded(draft, upgrade, excluded));
      }}
      onMove={(upgrade, step) => {
        const next = moveUpgrade(base, draft, upgrade, step);
        if (next === null) return null;
        onDraft(next);
        return (
          selectQueueEditor(base, next).entries.find((entry) => entry.id === upgrade)?.position ??
          null
        );
      }}
    />
  );
}

function Policy({
  policy,
  copyReason,
  onCopy,
}: {
  readonly policy: ShoppingLibraryPolicyVM;
  readonly copyReason: LibraryReason;
  readonly onCopy: () => void;
}) {
  const { commands } = useDashboard();
  const [confirming, setConfirming] = useState(false);
  const own = useId();
  useLayoutEffect(() => {
    if (confirming) document.getElementById(`${own}:cancel`)?.focus();
  }, [confirming, own]);
  const send = (command: ProfilePatchCommand | null) => {
    if (command !== null) void commands.patchProfile(command);
  };
  const apply = (next: QueueDraft) => {
    if (policy.revision === null) return;
    const pinned = pinLibrary(policy.baseValue, policy.revision, {
      ...policy.draft,
      library_id: policy.id,
      library_usage: JSON.stringify(policy.usage.map((run) => run.key)),
    });
    for (const [key, value] of draftChanges(policy.draft, { ...pinned, ...next }))
      commands.editDraft(policy.scope, key, value);
  };
  return (
    <div data-library-policy={policy.id}>
      <Tile
        as="fieldset"
        className="tp-library__policy"
        titleId={`${own}:title`}
        title={policy.name === null ? null : <ServerValue kind="name" value={policy.name} />}
      >
        {policy.usage.length === 0 ? (
          <p>{t.unused.short}</p>
        ) : (
          <div>
            <span>{t.usage.short}</span>
            <ul>
              {policy.usage.map((run) => (
                <li key={run.key}>
                  <a href={pageHref('runs', { kind: 'saved', run: run.key })}>
                    {run.name === null ? null : <ServerValue kind="name" value={run.name} />}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
        <a
          className="tp-library__link"
          href={pageHref(
            'runs',
            policy.usage[0] === undefined ? null : { kind: 'saved', run: policy.usage[0].key },
          )}
        >
          {t.binding.short}
        </a>
        <Disclosure
          label={t.edit.short}
          preview={<ValidationSummary problems={policyProblems(policy)} />}
        >
          {!policy.contextReady ? (
            <p role="status">{copy.runs.reasons.context_unknown.short}</p>
          ) : policy.base.plan !== null ? (
            <LibraryQueue
              base={policy.base}
              draft={policy.draft}
              vm={policy.queue}
              onDraft={apply}
            />
          ) : (
            <LegacyEditor policy={policy} onDraft={apply} />
          )}
          <div className="tp-library__actions tp-control-row">
            <Action
              label={t.save}
              reason={policy.reason}
              onClick={() => {
                send(policy.save);
              }}
            />
            <Button
              tier="quiet"
              label={t.discard.short}
              onClick={() => {
                commands.discardDraft(policy.scope);
              }}
            />
          </div>
        </Disclosure>
        <div className="tp-library__actions tp-control-row">
          <Action label={t.copy} reason={copyReason} onClick={onCopy} />
          <Action
            label={t.remove}
            id={`${own}:delete`}
            reason={policy.removeReason}
            onClick={() => {
              setConfirming(true);
            }}
          />
        </div>
        {confirming ? (
          <div role="group" aria-labelledby={`${own}:confirm`} className="tp-library__confirm">
            <h4 id={`${own}:confirm`}>{t.confirm.short}</h4>
            <p>{t.confirmNote.short}</p>
            <Action
              label={t.confirmDelete}
              reason={policy.removeReason}
              onClick={() => {
                send(policy.remove);
                setConfirming(false);
                document.getElementById('shopping-library-check')?.focus();
              }}
            />
            <Button
              tier="quiet"
              label={t.cancel.short}
              id={`${own}:cancel`}
              onClick={() => {
                setConfirming(false);
                document.getElementById(`${own}:delete`)?.focus();
              }}
            />
          </div>
        ) : null}
        {policy.outcome === null ? null : (
          <SaveOutcome outcome={policy.outcome} onDismiss={commands.dismiss} />
        )}
        {policy.continuation === null ? null : (
          <div>
            <p>{t.reconcileNote.short}</p>
            <Disclosure label={t.observed.short}>
              <pre>
                {policy.observed === null ? null : (
                  <ServerValue kind="detail" value={policy.observed} />
                )}
              </pre>
            </Disclosure>
            <Button
              tier="secondary"
              label={t.reconcile.short}
              onClick={() => {
                if (policy.continuation === null) return;
                for (const id of policy.continuation.unknownIds) commands.acceptCurrent(id);
                commands.editDraft(policy.scope, 'library_revision', policy.continuation.revision);
                commands.editDraft(
                  policy.scope,
                  'library_continued_after',
                  policy.continuation.after,
                );
              }}
            />
          </div>
        )}
      </Tile>
    </div>
  );
}

/** SQ9/SQ10: profile-scoped library, deliberately separate from a run's saved queue. */
export function ShoppingLibrary() {
  const vm = useView(selectShoppingLibrary);
  const { commands } = useDashboard();
  const create = vm.create;
  const problems =
    vm.policies.reduce((count, policy) => count + policyProblems(policy), 0) +
    (create.active
      ? (create.copying ? 0 : create.queue.review.problems) + (create.reason === 'id' ? 1 : 0)
      : 0);
  const apply = (next: QueueDraft) => {
    for (const [key, value] of draftChanges(create.draft, next))
      commands.editDraft(create.scope, key, value);
  };
  const stage = (value: unknown, from?: string) => {
    if (vm.revision === null) return;
    commands.discardDraft(create.scope);
    const pinned = pinLibrary(value, vm.revision, {});
    for (const [key, entry] of Object.entries({
      ...pinned,
      ...(from === undefined ? {} : { copy_from: from }),
    }))
      commands.editDraft(create.scope, key, entry);
  };
  return (
    <Region
      id="shoppingLibrary"
      title={t.title.short}
      fallback={<RevocationFallback part="runsSaved" />}
    >
      <RegionBody>
        <div className="tp-library" data-shopping-library>
          <p>{t.scope.short}</p>
          <Disclosure label={t.manage.short} preview={<ValidationSummary problems={problems} />}>
            {vm.notSent === null ? null : (
              <p data-library-not-sent>{t.notSent[vm.notSent].short}</p>
            )}
            <Facts>
              <Fact
                label={t.profile.short}
                value={vm.profile === null ? null : <ServerValue kind="name" value={vm.profile} />}
              />
            </Facts>
            {vm.readable ? null : <p>{(vm.unavailable ? t.unavailable : t.reading).short}</p>}
            {vm.error === null ? null : <ServerValue kind="message" value={vm.error} />}
            <Button
              id="shopping-library-check"
              tier="quiet"
              label={t.check.short}
              onClick={commands.checkCollection}
            />
            {vm.readable && vm.policies.length === 0 ? <p>{t.empty.short}</p> : null}
            <ShownList
              items={vm.policies}
              itemKey={(policy) => policy.scope}
              more={t.more.short}
              render={(policy) => (
                <Policy
                  key={policy.scope}
                  policy={policy}
                  copyReason={vm.gate ?? (create.active ? 'creation' : null)}
                  onCopy={() => {
                    stage(policy.latestValue, policy.id);
                  }}
                />
              )}
            />
            {vm.earlier.map((entry) => (
              <div key={entry.outcome.id} data-library-earlier={entry.policy}>
                {entry.name === null ? null : <ServerValue kind="name" value={entry.name} />}
                <SaveOutcome outcome={entry.outcome} onDismiss={commands.dismiss} />
              </div>
            ))}
            <Action
              label={t.create}
              reason={vm.gate ?? (create.active ? 'creation' : null)}
              onClick={() => {
                stage(vm.newValue);
                for (const [key, value] of Object.entries(stagePlan(vm.newValue)))
                  commands.editDraft(create.scope, key, value);
              }}
            />
            {!create.active || !vm.contextReady ? null : (
              <fieldset data-library-create>
                <legend>{(create.copying ? t.copyTitle : t.createTitle).short}</legend>
                <label>
                  <span>{t.id.short}</span>
                  <input
                    className="tp-field-control"
                    value={create.destination}
                    onChange={(event) => {
                      commands.editDraft(create.scope, 'id', event.currentTarget.value);
                    }}
                  />
                </label>
                {create.copying ? null : (
                  <LibraryQueue
                    base={create.base}
                    draft={create.draft}
                    vm={create.queue}
                    onDraft={apply}
                  />
                )}
                <div className="tp-library__actions tp-control-row">
                  <Action
                    label={t.save}
                    reason={create.reason}
                    onClick={() => {
                      if (create.save !== null) void commands.patchProfile(create.save);
                    }}
                  />
                  <Button
                    tier="quiet"
                    label={t.discard.short}
                    onClick={() => {
                      commands.discardDraft(create.scope);
                    }}
                  />
                </div>
              </fieldset>
            )}
            {create.outcome === null ? null : (
              <SaveOutcome outcome={create.outcome} onDismiss={commands.dismiss} />
            )}
            {create.continuation === null || !create.active ? null : (
              <div>
                <p>{t.reconcileNote.short}</p>
                <Button
                  tier="secondary"
                  label={t.reconcile.short}
                  onClick={() => {
                    if (create.continuation === null) return;
                    for (const id of create.continuation.unknownIds) commands.acceptCurrent(id);
                    commands.editDraft(
                      create.scope,
                      'library_revision',
                      create.continuation.revision,
                    );
                    commands.editDraft(
                      create.scope,
                      'library_continued_after',
                      create.continuation.after,
                    );
                  }}
                />
              </div>
            )}
          </Disclosure>
        </div>
      </RegionBody>
    </Region>
  );
}
