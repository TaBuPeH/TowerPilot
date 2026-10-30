import { useId } from 'react';
import { recognitionHref } from '../../../core/recognition.ts';
import { copy } from '../../../copy/catalogue.ts';
import type { CopyText } from '../../../copy/text.ts';
import { selectStartNext, type StartNextVM } from '../../../store/selectors/startNext.ts';
import { ActionRow } from '../../../ui/ActionRow.tsx';
import { Button } from '../../../ui/Button.tsx';
import { Chip } from '../../../ui/Chip.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { RegionBody } from '../../../ui/Region.tsx';
import { ServerOption, ServerValue } from '../../../ui/ServerValue.tsx';
import { useDashboard, useView } from '../context.ts';
import { DashboardRegion } from '../shared/DashboardRegion.tsx';
import './startNext.css';

const words = copy.dashboard.startNext;

type Reason = StartNextVM['reasons'][number];

function readinessText(line: StartNextVM['readiness']['line'], count: number): CopyText {
  return line === 'images_missing' ? words.imagesMissing(count) : words.readiness[line].short;
}

function reasonText(reason: Reason): CopyText {
  if (
    (reason.code === 'guard_blocks' || reason.code === 'guard_unconfirmed') &&
    reason.guardCode === 'setup_battle_unresolved'
  )
    return words.reasons.setup_battle_unresolved.short;
  switch (reason.code) {
    case 'pick_run':
    case 'sending':
    case 'input_off':
    case 'connection_blocked':
    case 'guard_blocks':
    case 'pick_single_run':
    case 'preview_unavailable':
    case 'context_changed':
    case 'gem_routines_off':
    case 'ownership_unconfirmed':
    case 'automation_listed':
      return words.reasons[reason.code].short;
    default:
      return words.reasons.guard_unconfirmed.short;
  }
}

/** Both actions blocked with the same words and details: that reason, said once, not twice. */
function sharedReason(reasons: StartNextVM['reasons']): Reason | null {
  const [first, second] = reasons;
  return reasons.length === 2 &&
    first !== undefined &&
    second !== undefined &&
    reasonText(first) === reasonText(second) &&
    first.guardCode === second.guardCode &&
    first.message === second.message
    ? first
    : null;
}

/**
 * One blocked action's reason on the reasons grid: the action's label in the
 * label column, Blocked and the reason beside it, its Why after. The id sits on
 * the line, so each button's description reads "{label}: Blocked {reason}".
 */
function ReasonLine({
  reason,
  id,
  label,
  focusKey,
}: {
  readonly reason: Reason;
  readonly id: string;
  readonly label: CopyText;
  readonly focusKey: string;
}) {
  return (
    <div className="tp-start-next__reason">
      <p id={id} data-guard-code={reason.guardCode ?? reason.code}>
        <span className="tp-start-next__label">{label}</span>
        {words.space}
        <span className="tp-start-next__value">
          <Chip state="blocked" />
          {words.space}
          <span>{reasonText(reason)}</span>
        </span>
      </p>
      {reason.message === null ? null : (
        <Disclosure label={copy.common.why.short} focusKey={focusKey}>
          <ServerValue value={reason.message} kind="message" />
        </Disclosure>
      )}
    </div>
  );
}

function ReadinessDetails({ details }: { readonly details: StartNextVM['readiness']['details'] }) {
  return (
    <ul>
      {details.map((detail, index) => (
        <li key={index}>
          <ServerValue
            value={detail.value}
            kind="message"
            {...(detail.kind === 'setting' ? { label: words.setting.short } : {})}
          />
        </li>
      ))}
    </ul>
  );
}

function StartNextBody() {
  const vm = useView(selectStartNext);
  const { commands } = useDashboard();
  const id = useId();
  const startReasonId = `${id}-start-reason`;
  const shared = sharedReason(vm.reasons);
  const collectReasonId = shared === null ? `${id}-collect-reason` : startReasonId;
  const startPermitted = vm.start.enabled;
  // "Ready" only while Start is permitted: never beside a Blocked line.
  const readinessShown = !(vm.readiness.line === 'ready' && !startPermitted);
  const readinessMore = vm.readiness.details.length > 0 || vm.readiness.imageCount > 0;
  return (
    <RegionBody>
      <div className="tp-start-next">
        <ActionRow
          reason={
            <div className="tp-start-next__reasons">
              {shared !== null ? (
                <ReasonLine
                  reason={shared}
                  id={startReasonId}
                  label={words.bothReason.short}
                  focusKey="startNext:start:why"
                />
              ) : (
                vm.reasons.map((reason) => (
                  <ReasonLine
                    key={reason.action}
                    reason={reason}
                    id={reason.action === 'start' ? startReasonId : collectReasonId}
                    label={
                      reason.action === 'start'
                        ? words.startReason.short
                        : words.collectReason.short
                    }
                    focusKey={`startNext:${reason.action}:why`}
                  />
                ))
              )}
              {readinessShown || readinessMore ? (
                <div className="tp-start-next__readiness">
                  {readinessShown ? (
                    <p data-readiness={vm.readiness.line}>
                      <span className="tp-start-next__label">
                        {vm.readiness.line === 'ready'
                          ? words.startReason.short
                          : words.readinessLabel.short}
                      </span>
                      {words.space}
                      <span className="tp-start-next__value">
                        {vm.readiness.line === 'readiness_check_failed' ||
                        vm.readiness.line === 'readiness_unknown' ? (
                          <Chip state="unknown" />
                        ) : null}
                        <span>{readinessText(vm.readiness.line, vm.readiness.imageCount)}</span>
                      </span>
                    </p>
                  ) : null}
                  {vm.readiness.line === 'not_ready' ? (
                    <ReadinessDetails details={vm.readiness.details} />
                  ) : vm.readiness.details.length > 0 ? (
                    <Disclosure label={copy.common.why.short} focusKey="startNext:readiness:why">
                      <ReadinessDetails details={vm.readiness.details} />
                    </Disclosure>
                  ) : null}
                  {vm.readiness.imageCount > 0 ? (
                    <a href={recognitionHref(vm.picker.selected)} data-focus-key="startNext:learn">
                      {words.learn.short}
                    </a>
                  ) : null}
                </div>
              ) : null}
            </div>
          }
        >
          <label className="tp-start-next__picker" htmlFor={`${id}-run`}>
            <span>{words.picker.short}</span>
            <select
              className="tp-field-control"
              id={`${id}-run`}
              value={vm.picker.selected ?? ''}
              data-focus-key="startNext:run"
              onChange={(event) => {
                commands.selectRun(event.currentTarget.value);
              }}
            >
              {vm.picker.runs.length === 0 ? <option value="">{words.empty.short}</option> : null}
              {vm.picker.runs.map((run) =>
                run.label === null ? (
                  <option key={run.name} value={run.name}>
                    {words.unnamedRun.short}
                  </option>
                ) : (
                  <ServerOption key={run.name} value={run.name} label={run.label} />
                ),
              )}
            </select>
          </label>
          <Button
            tier="primary"
            label={words.start.short}
            focusKey="startNext:start"
            busy={vm.start.sending}
            {...(startPermitted ? {} : { disabled: true, reasonId: startReasonId })}
            onClick={() => {
              if (vm.start.command !== null) void commands.start(vm.start.command);
            }}
          />
          <Button
            tier="secondary"
            label={words.collect.short}
            focusKey="startNext:collect"
            busy={vm.collect.sending}
            {...(vm.collect.enabled ? {} : { disabled: true, reasonId: collectReasonId })}
            onClick={() => {
              if (vm.collect.command !== null) void commands.collect(vm.collect.command);
            }}
          />
        </ActionRow>
        <div className="tp-start-next__foot">
          {vm.transferHint === null ? null : <p>{words.transfer[vm.transferHint].short}</p>}
          <a href={recognitionHref(vm.picker.selected)} data-focus-key="startNext:prepare">
            {words.prepare.short}
          </a>
        </div>
      </div>
    </RegionBody>
  );
}

/** Region 'startNext' (spec 2.1): owned by its region author. */
export function StartNext() {
  return (
    <DashboardRegion id="startNext">
      <StartNextBody />
    </DashboardRegion>
  );
}
