import { useId } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import type { CopyEntry, CopyText } from '../../../copy/text.ts';
import { selectGemSlot, type GemSlotVM } from '../../../store/selectors/gemSlot.ts';
import { Button } from '../../../ui/Button.tsx';
import { Disclosure } from '../../../ui/Disclosure.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { pageHref } from '../../../ui/routes.ts';
import { ServerOption, ServerValue } from '../../../ui/ServerValue.tsx';
import { SlotAction, SlotFacts, SlotMeta } from '../../../ui/Slot.tsx';
import { useDashboard, useView } from '../context.ts';
import { WhyTip } from '../shared/WhyTip.tsx';
import { codeParam, entryFor, numberParam, SeenLine, staleLine } from './seen.tsx';

const text = copy.dashboard.control.gem;
const shell = copy.dashboard.shell;

function choiceEntry(vm: GemSlotVM): CopyEntry {
  if (vm.choice.qualifier === 'from_before_turn_on') return text.choice.from_before_turn_on;
  return text.choice[vm.choice.word];
}

/** The Seen now line of spec 3.6.2 (G1 to G5g), and its second line when there is one. */
function seenLine(vm: GemSlotVM): {
  readonly line: CopyText;
  readonly extra: CopyText | null;
  readonly why: CopyText | undefined;
} {
  const chip = vm.seen;
  const lines: Readonly<Record<string, CopyEntry>> = text.lines;
  switch (chip.line) {
    case 'last_read':
      return {
        line: staleLine(chip) ?? text.lines.state_unknown.short,
        extra: null,
        why: undefined,
      };
    case 'claims_failing': {
      const streak = numberParam(chip, 'streak');
      return {
        line: streak === null ? text.lines.claims_failing.short : text.claimsFailing(streak),
        extra: null,
        why: text.lines.claims_failing.why,
      };
    }
    case 'collecting_gems':
      return {
        line: text.lines.collecting_gems.short,
        extra:
          chip.params['collectOnly'] === true ? text.lines.started_by_collect_only.short : null,
        why: undefined,
      };
    case 'another_process_holds': {
      const pid = numberParam(chip, 'pid');
      return {
        line: (chip.params['foreign'] === true
          ? text.lines.another_install_holds
          : text.lines.another_process_holds
        ).short,
        extra: pid === null ? null : text.holderPid(pid),
        why: undefined,
      };
    }
    case 'blocked_cause': {
      const cause = entryFor(text.blocked, codeParam(chip, 'code'), text.blocked.other);
      return { line: cause.short, extra: null, why: cause.why };
    }
    case 'retrying_in': {
      const seconds = numberParam(chip, 'seconds');
      return {
        line:
          seconds === null ? text.lines.retry_time_not_reported.short : text.retryingIn(seconds),
        extra: null,
        why: undefined,
      };
    }
    default: {
      const found = entryFor(lines, chip.line, text.lines.state_unknown);
      return { line: found.short, extra: null, why: found.why };
    }
  }
}

/**
 * This battle's counts as facts: the backend's values, worded in the catalogue.
 * Nothing when the row does not say gems are being handled.
 */
function outcomeFacts(vm: GemSlotVM) {
  const outcomes = vm.outcomes;
  if (outcomes === null) return null;
  const ad = text.outcomes.ad(outcomes.adConfirmations, outcomes.windowsMissed);
  return (
    <>
      {ad === null ? null : (
        <Fact label={text.facts.adGems.short} value={<span data-gem-counts="ad">{ad}</span>} />
      )}
      {outcomes.orbitConfirmations === null ? null : (
        <Fact
          label={text.facts.orbitGems.short}
          value={
            <span data-gem-counts="orbit">{text.outcomes.orbit(outcomes.orbitConfirmations)}</span>
          }
        />
      )}
    </>
  );
}

/** The target already written in the This battle fact; unknown targets stay unknown. */
function overlayTarget(vm: GemSlotVM, routine: 'ad_gems' | 'floating_gems'): boolean | null {
  return vm.overlay?.kind === 'reported'
    ? (vm.overlay.routines.find((entry) => entry.routine === routine)?.target ?? null)
    : null;
}

/** F-030: this battle's gem toggles, restated as reported (S-036); never a token value. */
function overlayFact(vm: GemSlotVM) {
  const overlay = vm.overlay;
  if (overlay === null || overlay.kind !== 'reported') return null;
  const liveChoice = overlay.routines.some((entry) => entry.skipped === 'live_choice');
  const unsupported = overlay.routines.some((entry) => entry.skipped === 'unsupported');
  return (
    <Fact
      label={text.facts.thisBattle.short}
      value={
        <span className="tp-gem-list" data-overlay={overlay.applied ? 'applied' : 'pending'}>
          <span>
            {text.overlay.routine(overlayTarget(vm, 'ad_gems'), overlayTarget(vm, 'floating_gems'))}
          </span>
          <span>{(overlay.applied ? text.overlay.applied : text.overlay.pending).short}</span>
          {liveChoice ? <span>{text.overlay.liveChoice.short}</span> : null}
          {unsupported ? <span>{text.overlay.unsupported.short}</span> : null}
        </span>
      }
    />
  );
}

/**
 * The slot's one Details: diagnostics and provenance kept off the glance - the
 * latest missed ad window, that an applied battle setting keeps its own timing,
 * and the collector the dashboard found at its start, with its PID.
 */
function GemDetails({ vm }: { readonly vm: GemSlotVM }) {
  const cause = vm.outcomes?.cause ?? null;
  const ownTiming = vm.overlay?.kind === 'reported' && vm.overlay.applied;
  const found = vm.collectorAtStart;
  if (cause === null && !ownTiming && found === null) return null;
  const words =
    found === null
      ? null
      : entryFor(text.collectorAtStart, found.code, text.collectorAtStart.other);
  return (
    <Disclosure label={text.details.short} focusKey="gem:details">
      {cause === null ? null : (
        <Facts>
          <Fact
            label={text.facts.lastMiss.short}
            value={
              <span data-gem-cause={cause.code}>
                {text.outcomes.cause(cause.code, cause.agoSec)}
              </span>
            }
          />
        </Facts>
      )}
      {ownTiming ? <p data-overlay-timing="">{text.overlay.ownTiming.short}</p> : null}
      {found === null || words === null ? null : (
        <p className="tp-gem-list" data-collector-at-start={found.code}>
          <span>{words.short}</span>
          {found.pid === null ? null : <span>{text.holderPid(found.pid)}</span>}
        </p>
      )}
    </Disclosure>
  );
}

/**
 * F-025, S-080: which run the gem settings come from (a link to it on Runs) and
 * their timing. The Timing value follows the picker's value at once and
 * describes the picker and Turn on; beside the picker the run is already on
 * screen, so its name is kept for those controls but not drawn twice.
 */
function sourceFacts(vm: GemSlotVM, id: string) {
  const source = vm.source;
  if (source.value === null) return null;
  const invalid = vm.cause?.kind === 'source_invalid' && source.isServerSource;
  const pickerShown = source.pickerShown && source.choices.length > 0;
  return (
    <>
      {pickerShown ? null : (
        <Fact
          label={text.facts.run.short}
          value={
            <a href={pageHref('runs', { kind: 'saved', run: source.value })}>
              {source.label === null ? (
                text.unnamedRun.short
              ) : (
                <ServerValue kind="name" value={source.label} />
              )}
            </a>
          }
        />
      )}
      <Fact
        label={text.facts.timing.short}
        value={
          <span className="tp-source tp-gem-list" id={id}>
            {pickerShown ? (
              <span className="tp-visually-hidden">
                <span>{text.sourcePicker.short}</span>
                {source.label === null ? null : <ServerValue kind="name" value={source.label} />}
              </span>
            ) : null}
            <span>
              {invalid
                ? text.sourceInvalid.short
                : source.policy !== null
                  ? text.sourceRoutines(source.policy.adGems, source.policy.floatingGems, {
                      adGems: overlayTarget(vm, 'ad_gems'),
                      floatingGems: overlayTarget(vm, 'floating_gems'),
                    })
                  : source.policyPending
                    ? text.sourceNotRead.short
                    : text.sourceUnreadable.short}
            </span>
            {vm.sourceChanged ? (
              <span className="tp-seen__extra">{text.sourceChanged.short}</span>
            ) : null}
          </span>
        }
      />
    </>
  );
}

/**
 * Control / Gem harvesting (spec 3.6.2, INV-1; F-023 to F-032, F-036): one
 * status line (Seen now: a chip from the gem table alone, with its Why), then
 * facts - Setting (the choice, from D-1 alone, never an input to the chip,
 * S-034), this battle's counts, the settings source and timing - and a footer
 * of Details and the Saved settings link. Pause
 * harvesting (halt tier) whenever S-001 holds, enabled whenever shown; while
 * the choice is confirmed Off or Not set, the "Gem settings from" picker and
 * Turn on with its reason.
 */
export function GemSlot() {
  const vm = useView(selectGemSlot);
  const { commands } = useDashboard();
  const own = useId();
  const seen = seenLine(vm);
  const choice = choiceEntry(vm);
  const pickerId = `${own}-picker`;
  const sourceId = `${own}-source`;
  const turnOnReasonId = `${own}-turn-on-reason`;
  const againReasonId = `${own}-again-reason`;
  const turnOnReasons: Readonly<Record<string, CopyEntry>> = text.turnOnReasons;
  const reasonOf = (code: string | null) =>
    entryFor(turnOnReasons, code, text.turnOnReasons.choice_unknown);
  const turnOnCommand = vm.turnOn.command;
  const againCommand = vm.turnOnAgain.command;
  return (
    <>
      <SlotAction>
        {vm.pause.shown ? (
          <Button
            tier="halt"
            label={shell.revocations.pause.short}
            focusKey="gem:pause"
            busy={vm.pause.sending}
            onClick={() => {
              void commands.pause();
            }}
          />
        ) : null}
        {vm.source.pickerShown && vm.source.choices.length > 0 ? (
          <span className="tp-picker">
            <label htmlFor={pickerId}>{text.sourcePicker.short}</label>
            <select
              className="tp-field-control"
              id={pickerId}
              data-focus-key="gem:source"
              value={vm.source.value ?? ''}
              aria-describedby={sourceId}
              onChange={(event) => {
                commands.selectGemSource(event.currentTarget.value);
              }}
            >
              {vm.source.choices.map((choiceOption) =>
                choiceOption.label === null ? (
                  <option key={choiceOption.key} value={choiceOption.key}>
                    {text.unnamedRun.short}
                  </option>
                ) : (
                  <ServerOption
                    key={choiceOption.key}
                    value={choiceOption.key}
                    label={choiceOption.label}
                  />
                ),
              )}
            </select>
            {vm.source.value === null ? null : (
              <a href={pageHref('runs', { kind: 'saved', run: vm.source.value })}>
                {text.editRun.short}
              </a>
            )}
          </span>
        ) : null}
        {vm.turnOn.shown && vm.source.pickerShown ? (
          vm.turnOn.enabled && turnOnCommand !== null ? (
            <Button
              tier="secondary"
              label={text.turnOn.short}
              focusKey="gem:turn-on"
              busy={vm.turnOn.sending}
              describedBy={sourceId}
              onClick={() => {
                void commands.turnOn(turnOnCommand);
              }}
            />
          ) : (
            <>
              <Button
                tier="secondary"
                label={text.turnOn.short}
                focusKey="gem:turn-on"
                busy={vm.turnOn.sending}
                disabled
                reasonId={turnOnReasonId}
                onClick={() => undefined}
              />
              <span id={turnOnReasonId} className="tp-reason">
                {reasonOf(vm.turnOn.reason).short}
              </span>
            </>
          )
        ) : null}
        {vm.turnOnAgain.shown ? (
          vm.turnOnAgain.enabled && againCommand !== null ? (
            <Button
              tier="secondary"
              label={text.turnOnAgain.short}
              focusKey="gem:turn-on-again"
              busy={vm.turnOnAgain.sending}
              onClick={() => {
                void commands.turnOn(againCommand);
              }}
            />
          ) : (
            <>
              <Button
                tier="secondary"
                label={text.turnOnAgain.short}
                focusKey="gem:turn-on-again"
                disabled
                reasonId={againReasonId}
                onClick={() => undefined}
              />
              <span id={againReasonId} className="tp-reason">
                {reasonOf(vm.turnOnAgain.reason).short}
              </span>
            </>
          )
        ) : null}
      </SlotAction>
      <SlotFacts>
        <div className="tp-automation-state-line">
          {/* "Collecting gems" is what Running means here: one chip says it, not a chip and a line. */}
          {vm.seen.line === 'collecting_gems' && vm.seen.word === 'running' ? (
            <SeenLine chip={vm.seen} chipLabel={seen.line} line={null} extra={seen.extra} />
          ) : (
            <SeenLine chip={vm.seen} line={seen.line} extra={seen.extra} />
          )}
          {vm.cause?.message === null || vm.cause === null ? null : (
            <details className="tp-copy-line__why">
              <summary>{copy.common.why.short}</summary>
              <ServerValue kind="message" value={vm.cause.message} />
            </details>
          )}
          <WhyTip subject={seen.line} why={seen.why} />
        </div>
        {vm.outcomes?.finderSuspected === true ? (
          <p className="tp-seen__extra" data-gem-finder-suspected="">
            {text.finderSuspected.short}
          </p>
        ) : null}
        <Facts layout="inline">
          {/* Setting On beside a running collector says nothing the chip and Pause don't. */}
          {vm.choice.word === 'on' &&
          vm.choice.qualifier !== 'from_before_turn_on' &&
          vm.seen.word === 'running' ? null : (
            <Fact
              label={text.facts.setting.short}
              value={<span data-fact="setting">{choice.short}</span>}
            />
          )}
          {overlayFact(vm)}
          {outcomeFacts(vm)}
          {sourceFacts(vm, sourceId)}
        </Facts>
      </SlotFacts>
      <SlotMeta>
        <GemDetails vm={vm} />
      </SlotMeta>
    </>
  );
}
