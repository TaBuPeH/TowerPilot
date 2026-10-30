import { confirmStopped } from '../core/evidence.ts';
import { gateOf, type GateRead, type ReadAssessment } from '../core/gate.ts';
import { offConfirmed } from '../core/harvestChoice.ts';
import { turnOffSuperseded } from '../core/inputFacts.ts';
import { leaseHolders, leaseResolvedBy } from '../core/lease.ts';
import { classifyHarvestAnswer, settleLostTurnOn } from '../core/outcomes/harvest.ts';
import { classifyInputAnswer } from '../core/outcomes/input.ts';
import { classifyStopAnswer } from '../core/outcomes/stop.ts';
import { own } from '../core/read.ts';
import { runAddRecorded, runAddSource } from '../core/runAdd.ts';
import { settleProfilePatch } from '../core/profilePatch.ts';
import { settleSettingsSave } from '../core/settingsFields.ts';
import { settleAdopt, settleInstall } from '../core/emulators.ts';
import { applyRecordedIn, launchRecordedIn, stopRecordedIn } from '../core/recognition.ts';
import {
  profileWriteKind,
  settlementScope,
  settleProfileWrite,
  unbindUnresolved,
  type ProfileWriteKind,
} from '../core/profiles.ts';
import { settleCommand, settleRunNow } from '../core/routines.ts';
import { contextsEqual } from '../core/readiness.ts';
import { savedReadback, savedSaveAnswer, settleSavedSave } from '../core/runControls.ts';
import { opaqueToken } from '../core/tokens.ts';
import { assessedRead, contextReadOf } from './reads.ts';
import type { ControlFacts } from './selectors/controlFacts.ts';
import type {
  DashboardState,
  IntentView,
  ReadSettlementCode,
  RetainedSettlement,
} from './types.ts';

/**
 * Feedback fences (safety rules S-011, S-013, S-046, S-047, D-10; spec 7.4
 * "Settled by"). An outcome line is current only while nothing newer replaced
 * it: a newer request on the same target (S-011), a newer choice (S-013: a
 * higher revision than the receipt's, whenever the read that showed it
 * arrived), or a newer input fact (S-047). A record whose outcome is unknown
 * is settled by a later read only through the evidence its 7.4 row names, a
 * read STARTED after the record's own answer when it has one (B-01: an answer
 * is newer evidence than any read started before it); a read that is not that
 * evidence leaves it unknown (OD-9). Once proven, a settlement is kept by the
 * store (`DashboardState.readSettlements`): a later failed read never undoes it.
 */

/** Kill: whether a later D-4 read still lists that pid and created (display only, never "killed"). */
export type KillListing = 'still_listed' | 'not_listed';

/** Stop answers after which a later read may still prove the operation gone. */
const STOP_IN_PROGRESS: readonly string[] = [
  'stopping',
  'unverified',
  'partial',
  'active',
  'unknown',
];

function unresolved(record: IntentView): boolean {
  return record.phase === 'sending' || record.phase === 'unconfirmed';
}

/** The newest status read, when it was started after `seq`. */
function laterStatus(state: DashboardState, seq: number): ReadAssessment | null {
  const latest = state.reads.status.latest;
  if (latest === null || latest.startedSeq <= seq) return null;
  return assessedRead(latest);
}

/** The point a later read must start after: the record's answer, or its latest send. */
function judgedAfter(record: IntentView): number {
  return record.answerSeq ?? record.sentSeq;
}

/** A Stop whose answer named lease holders is settled only once a D-4 read resolves each one (S-053). */
function stopHoldersResolved(record: IntentView, gate: GateRead): boolean {
  if (record.answer === null || record.answerSeq === null) return true;
  const holder = own(own(record.answer.body, 'stop'), 'holder');
  if (own(holder, 'lease') === undefined) return true;
  return (
    leaseHolders(holder).length > 0 &&
    leaseResolvedBy({ owner: holder, learnedSeq: record.answerSeq }, gate)
  );
}

/** True when `record` was made for the emulator the page shows (its account may differ). */
export function sameEmulator(state: DashboardState, record: IntentView): boolean {
  const now = state.context;
  return (
    now !== null &&
    record.context !== null &&
    record.context.instance === now.instance &&
    record.context.serial === now.serial
  );
}

/**
 * Whether the page's current context shares with `record` the part a Profiles read
 * describes (core/profiles.ts settlementScope): the emulator for the account writes,
 * also the account for the file writes, also the profile for the tier.
 */
function sameSettlementScope(
  state: DashboardState,
  record: IntentView,
  kind: ProfileWriteKind,
): boolean {
  if (!sameEmulator(state, record)) return false;
  const scope = settlementScope(kind);
  if (scope === 'emulator') return true;
  const now = state.context;
  const then = record.context;
  if (now === null || then === null || now.account !== then.account) return false;
  return scope === 'account' || now.profile === then.profile;
}

/** What the newest read proves about `record`'s unknown outcome now (null: nothing). */
export function candidateSettlement(
  state: DashboardState,
  facts: ControlFacts,
  record: IntentView,
): RetainedSettlement | null {
  const after = judgedAfter(record);
  const status = laterStatus(state, after);
  const found = (settlement: ReadSettlementCode, atSeq: number): RetainedSettlement => ({
    settlement,
    atSeq,
  });
  switch (record.kind) {
    case 'pause': {
      if (!unresolved(record) || status === null) return null;
      if (gateOf(status, after).kind === 'unknown') return null;
      return offConfirmed(status.harvest, facts.knownOn, facts.unsettled)
        ? found('off_confirmed', status.startedSeq)
        : null;
    }
    case 'turn_on': {
      const token = opaqueToken(record.key);
      const expected = record.meta.expectedRevision;
      if (
        !unresolved(record) ||
        status === null ||
        status.harvest.kind === 'unknown' ||
        token === null ||
        expected === undefined
      ) {
        return null;
      }
      const settled = settleLostTurnOn(status.harvest.value, expected, token);
      return settled === 'unknown' ? null : found(settled, status.startedSeq);
    }
    case 'stop': {
      const operationId = record.meta.operationId;
      if (status === null || operationId === undefined) return null;
      if (
        record.answer !== null &&
        !STOP_IN_PROGRESS.includes(classifyStopAnswer(record.answer, operationId).kind)
      ) {
        return null;
      }
      const gate = gateOf(status, after);
      return gate.kind === 'known' &&
        confirmStopped({ operationId, pid: null }, gate.value).kind === 'known' &&
        stopHoldersResolved(record, gate.value)
        ? found('stopped', status.startedSeq)
        : null;
    }
    case 'handoff': {
      if (!unresolved(record) || status === null || !status.answered) return null;
      const guard = own(status.body, 'collection_guard');
      return own(guard, 'blocks_launch') === false &&
        own(guard, 'operation_id') === record.meta.operationId &&
        own(guard, 'evidence_revision') === record.meta.evidenceRevision
        ? found('recorded', status.startedSeq)
        : null;
    }
    case 'run_now': {
      const read = state.reads.battlesActive.latest;
      const token = opaqueToken(record.key);
      const { battleId, generation, operationId } = record.meta;
      if (
        !unresolved(record) ||
        read === null ||
        read.startedSeq <= after ||
        token === null ||
        battleId === undefined ||
        generation === undefined ||
        operationId === undefined
      ) {
        return null;
      }
      return settleRunNow(contextReadOf(read), { id: battleId, generation, operationId }, token)
        .kind === 'recorded'
        ? found('recorded', read.startedSeq)
        : null;
    }
    case 'live_controls': {
      // Runs brief RUN-08: a later battle read lists a controls command carrying this key.
      const read = state.reads.battlesActive.latest;
      const token = opaqueToken(record.key);
      const { battleId, generation, operationId } = record.meta;
      if (
        !unresolved(record) ||
        read === null ||
        read.startedSeq <= after ||
        token === null ||
        battleId === undefined ||
        generation === undefined ||
        operationId === undefined
      ) {
        return null;
      }
      return settleCommand(
        contextReadOf(read),
        { id: battleId, generation, operationId },
        token,
        'controls',
      ).kind === 'recorded'
        ? found('recorded', read.startedSeq)
        : null;
    }
    case 'account_select':
    case 'account_create':
    case 'account_tier':
    case 'profile_activate':
    case 'profile_copy':
    case 'profile_promote': {
      // Profiles (P1 to P5): a later read under the write's settlement scope (F6P-03).
      // F6P-R2-01: accounts may report a fallback tier; only the original profile's
      // readable source can confirm an explicitly saved player.max_tier.
      const unbind = record.kind === 'profile_activate' && record.meta.unbind === true;
      if (
        (unbind ? record.phase === 'sending' || !unbindUnresolved(record) : !unresolved(record)) ||
        !sameSettlementScope(state, record, record.kind)
      )
        return null;
      const resource =
        record.kind === 'profile_activate' && record.meta.unbind === true
          ? 'config'
          : record.kind === 'account_tier'
            ? 'profileSource'
            : record.kind.startsWith('account_')
              ? 'accounts'
              : 'profileFiles';
      const read = state.reads[resource].latest;
      if (
        read === null ||
        read.startedSeq <= (unbind ? Math.max(after, record.lostSeq ?? 0) : after) ||
        read.outcome.kind !== 'answered' ||
        read.outcome.status !== 200
      )
        return null;
      const settled = settleProfileWrite(
        record.kind,
        { ...record.meta, profile: record.context?.profile, context: record.context },
        {
          accounts: resource === 'accounts' ? read.outcome.body : undefined,
          files: resource === 'profileFiles' ? read.outcome.body : undefined,
          source: resource === 'profileSource' ? read.outcome.body : undefined,
          config: resource === 'config' ? read.outcome.body : undefined,
        },
      );
      return settled === null ? null : found(settled, read.startedSeq);
    }
    case 'run_save': {
      // F6R-03: a refused conflict (409) wrote nothing itself; a later read proving the saved
      // settings hold every sent value says they now have the changes (never a resend).
      if (record.phase === 'refused') {
        const readback = refusedSaveReadback(state, record);
        return readback?.readback === 'applied' ? found('applied', readback.atSeq) : null;
      }
      // Runs brief RUN-07: a later read of the same run (started after the save) settles it.
      const { blueprint, profileRevision, changes } = record.meta;
      if (
        !unresolved(record) ||
        blueprint === undefined ||
        profileRevision === undefined ||
        changes === undefined
      ) {
        return null;
      }
      for (const read of Object.values(state.runControls)) {
        if (read.startedSeq <= after) continue;
        const context = contextReadOf(read);
        if (context?.kind !== 'answered' || !context.sameContext) continue;
        if (
          Object.hasOwn(changes, 'shopping_plan') &&
          !contextsEqual(own(context.body, 'context'), record.context, [
            'instance',
            'account',
            'profile',
          ])
        )
          continue;
        const settled = settleSavedSave(context.body, { blueprint, profileRevision, changes });
        if (settled !== null) return found(settled, read.startedSeq);
      }
      return null;
    }
    case 'adopt': {
      // M2: a later config read (started after the answer or its loss) of the same instance:
      // the instance's address is the sent one, or the configuration did not move.
      if (record.phase !== 'unconfirmed') return null;
      const point = Math.max(after, record.answerSeq ?? record.lostSeq ?? 0);
      const read = state.reads.config.latest;
      if (
        read === null ||
        read.startedSeq <= point ||
        read.outcome.kind !== 'answered' ||
        read.outcome.status !== 200 ||
        record.context?.instance !== state.context?.instance
      )
        return null;
      const settled = settleAdopt(read.outcome.body, record.meta);
      return settled === null ? null : found(settled, read.startedSeq);
    }
    case 'tools_install': {
      // M5: a later tools read: a download running or done means the install was taken.
      if (record.phase !== 'unconfirmed') return null;
      const point = Math.max(after, record.answerSeq ?? record.lostSeq ?? 0);
      const read = state.reads.wizardTools.latest;
      if (
        read === null ||
        read.startedSeq <= point ||
        read.outcome.kind !== 'answered' ||
        read.outcome.status !== 200
      )
        return null;
      const settled = settleInstall(read.outcome.body);
      return settled === null ? null : found(settled, read.startedSeq);
    }
    case 'calibrate_start':
    case 'calibrate_observe':
    case 'calibrate_apply':
    case 'calibrate_stop': {
      // Recognition (R1, R4, R5, R8): a request whose answer was lost is settled only by a
      // calibrator status read (by operation id, or the latest of the context) started after
      // the loss, under the same emulator and account, that records it: an operation carrying
      // the launch's key, the operation's own apply of the sent revision, or the operation
      // moved past the sent revision into stopping or stopped. Anything else keeps it unknown.
      if (record.phase !== 'unconfirmed') return null;
      const then = record.context;
      if (then === null || !sameEmulator(state, record) || state.context?.account !== then.account)
        return null;
      const point = Math.max(after, record.answerSeq ?? record.lostSeq ?? 0);
      const { operationId, expectedRevision } = record.meta;
      for (const read of [state.reads.calibrateOperation.latest, state.reads.calibrate.latest]) {
        if (
          read === null ||
          read.startedSeq <= point ||
          read.outcome.kind !== 'answered' ||
          read.outcome.status !== 200
        )
          continue;
        const body = read.outcome.body;
        if (record.kind === 'calibrate_apply') {
          if (
            operationId !== undefined &&
            expectedRevision !== undefined &&
            applyRecordedIn(body, operationId, expectedRevision)
          )
            return found('applied', read.startedSeq);
        } else if (record.kind === 'calibrate_stop') {
          if (
            operationId !== undefined &&
            expectedRevision !== undefined &&
            stopRecordedIn(body, operationId, expectedRevision)
          )
            return found('recorded', read.startedSeq);
        } else if (record.key !== null && launchRecordedIn(body, record.key) !== null) {
          return found('recorded', read.startedSeq);
        }
      }
      return null;
    }
    case 'run_add': {
      const then = record.context;
      if (
        record.phase !== 'unconfirmed' ||
        then === null ||
        !sameEmulator(state, record) ||
        state.context?.account !== then.account ||
        state.context.profile !== then.profile
      )
        return null;
      const read = state.reads.profileSource.latest;
      const point = Math.max(after, record.answerSeq ?? record.lostSeq ?? 0);
      return read !== null &&
        read.startedSeq > point &&
        read.outcome.kind === 'answered' &&
        read.outcome.status === 200 &&
        then.profile !== null &&
        runAddSource(read.outcome.body, then.profile) &&
        runAddRecorded(read.outcome.body, record.meta)
        ? found('applied', read.startedSeq)
        : null;
    }
    case 'settings_save':
    case 'profile_patch': {
      // Collection (C1 to C6): a later read of the settings (a settings save) or of the
      // profile source (a profile patch), started after the answer or the loss.
      // Settings saves intentionally allow a stale identical resend to settle from
      // matching values. Every definite profile-patch refusal stays refused. Unknown
      // patches and inactive entry receipts use the later named, advanced source read.
      const conflict =
        record.kind === 'settings_save' &&
        record.phase === 'refused' &&
        record.answer?.status === 409;
      // C15-4 is the sole intentional context transition. Its receipt stays attached
      // to the submitting context, and only a config read begun after it proves it.
      if (record.kind === 'settings_save' && record.meta.module === 'active-instance') {
        const read = state.reads.config.latest;
        const then = record.context;
        if (
          record.phase === 'sending' ||
          (record.phase === 'refused' && !conflict) ||
          then === null ||
          read === null ||
          read.startedSeq <= Math.max(after, record.answerSeq ?? record.lostSeq ?? 0) ||
          read.outcome.kind !== 'answered' ||
          read.outcome.status !== 200
        )
          return null;
        const settled = settleSettingsSave(read.outcome.body, record.meta, then);
        return settled === null ? null : found(settled, read.startedSeq);
      }
      // F6C-01: nothing settles a write still in flight, and only a read started after its
      // answer, or after the page learned the answer was lost, can (an earlier read may
      // predate the server's commit).
      // PE6: even a successful inactive entry receipt needs its named source read
      // before the editor reports Saved. Reuse the same path/revision proof as a loss.
      const inactiveEntryReceipt =
        record.kind === 'profile_patch' &&
        record.phase === 'confirmed' &&
        record.target.startsWith('runs:entry:') &&
        record.meta.name !== record.context?.profile;
      if (record.phase !== 'unconfirmed' && !conflict && !inactiveEntryReceipt) return null;
      // F6C-R1-05: only a read made under the write's own emulator, account and profile.
      const then = record.context;
      if (
        then === null ||
        !sameEmulator(state, record) ||
        state.context?.account !== then.account ||
        state.context.profile !== then.profile
      )
        return null;
      const point = Math.max(after, record.answerSeq ?? record.lostSeq ?? 0);
      // F6-14: a patch of a profile other than the active one (the editing target) is
      // settled by that profile's own source read; settleProfilePatch checks the name.
      const read =
        record.kind === 'settings_save'
          ? state.reads.settings.latest
          : record.meta.name !== undefined && record.meta.name !== then.profile
            ? state.reads.targetSource.latest
            : state.reads.profileSource.latest;
      if (
        read === null ||
        read.startedSeq <= point ||
        read.outcome.kind !== 'answered' ||
        read.outcome.status !== 200
      )
        return null;
      const { module, values, baseRevision, patchPath, patchRemove, patchValue, name } =
        record.meta;
      // F6C-R1-02: a read only ever proves the values arrived; an unchanged one keeps the
      // write unknown (it may still commit), with its journal and its draft.
      const settled =
        record.kind === 'settings_save'
          ? settleSettingsSave(
              read.outcome.body,
              { module, values, revision: baseRevision },
              { instance: then.instance, account: then.account, profile: then.profile },
            )
          : patchPath === undefined
            ? null
            : settleProfilePatch(read.outcome.body, {
                path: patchPath,
                remove: patchRemove === true,
                value: patchValue,
                revision: baseRevision ?? null,
                name: name ?? then.profile ?? undefined,
              });
      return settled === null ? null : found(settled, read.startedSeq);
    }
    default:
      return null;
  }
}

/**
 * F6R-03: what the newest read of its run, started after the answer, says about the values
 * of a saved-run save refused as a conflict (409): `applied`, `changed` or `unchanged`
 * (core savedReadback). Null for any other record, or before such a read.
 */
export function refusedSaveReadback(
  state: DashboardState,
  record: IntentView,
): { readonly readback: 'applied' | 'changed' | 'unchanged'; readonly atSeq: number } | null {
  const { blueprint, profileRevision, changes } = record.meta;
  if (
    record.kind !== 'run_save' ||
    record.phase !== 'refused' ||
    record.answer === null ||
    blueprint === undefined ||
    profileRevision === undefined ||
    changes === undefined
  )
    return null;
  const answer = savedSaveAnswer(record.answer, record.meta);
  // SQ12: a shopping resend refused stale is still refused. Only the original Unknown
  // request can be settled by a read; a refusal never becomes a shopping receipt.
  if (Object.hasOwn(changes, 'shopping_plan')) return null;
  if (answer.kind !== 'refused' || !answer.conflict) return null;
  let newest: {
    readonly readback: 'applied' | 'changed' | 'unchanged';
    readonly atSeq: number;
  } | null = null;
  for (const read of Object.values(state.runControls)) {
    if (
      read.startedSeq <= judgedAfter(record) ||
      (newest !== null && read.startedSeq < newest.atSeq)
    )
      continue;
    const context = contextReadOf(read);
    if (context?.kind !== 'answered' || !context.sameContext) continue;
    const readback = savedReadback(context.body, { blueprint, profileRevision, changes });
    if (readback !== null) newest = { readback, atSeq: read.startedSeq };
  }
  return newest;
}

/**
 * The settlements kept after an update (the reducer): a kept one is dropped when the
 * record's own answer arrived after the read that proved it (B-01), then every record
 * of this context without one is judged against the newest reads.
 */
export function nextSettlements(
  state: DashboardState,
  facts: ControlFacts,
): Readonly<Record<string, RetainedSettlement>> {
  // Until a config read of this page names the context, a read may describe another emulator
  // (the session record is only a guess): it settles nothing.
  if (!state.contextConfirmed) return state.readSettlements;
  const next: Record<string, RetainedSettlement> = {};
  let changed = false;
  // A Profiles write may have changed the account itself: its record, made in the account
  // before, is judged by this emulator's reads whatever account the page shows now.
  const profileWrites = state.intents.filter(
    (record) =>
      ((profileWriteKind(record.kind) && sameEmulator(state, record)) ||
        // An adopt rewrites the address of its instance: judged by that instance's reads.
        (record.kind === 'adopt' && record.context?.instance === state.context?.instance) ||
        // C15-4: an intentional selection is judged across its old-to-new context change.
        (record.kind === 'settings_save' && record.meta.module === 'active-instance')) &&
      !facts.records.some((own) => own.id === record.id),
  );
  for (const record of [...facts.records, ...profileWrites]) {
    const kept = state.readSettlements[record.id];
    if (kept !== undefined && (record.answerSeq === null || record.answerSeq < kept.atSeq)) {
      next[record.id] = kept;
      continue;
    }
    if (kept !== undefined) changed = true;
    const found = candidateSettlement(state, facts, record);
    if (found !== null) {
      next[record.id] = found;
      changed = true;
    }
  }
  if (!changed && Object.keys(next).length === Object.keys(state.readSettlements).length) {
    return state.readSettlements;
  }
  return Object.freeze(next);
}

/** What a later read settled for `record`: kept by the store, or resolved by the machine. */
export function settledByRead(
  state: DashboardState,
  record: IntentView,
): ReadSettlementCode | null {
  return state.readSettlements[record.id]?.settlement ?? record.readSettlement;
}

/** Kill (S-060): whether the newest D-4 read started after it still lists that exact process. */
export function killListing(state: DashboardState, record: IntentView): KillListing | null {
  if (record.kind !== 'kill') return null;
  const status = laterStatus(state, judgedAfter(record));
  if (status === null) return null;
  const gate = gateOf(status, judgedAfter(record));
  if (gate.kind === 'unknown') return null;
  return gate.value.ownership.processes.some(
    (row) => row.pid === record.meta.pid && row.created === record.meta.created,
  )
    ? 'still_listed'
    : 'not_listed';
}

/** True while `record`'s line is the current result of its target (else it is history). */
export function lineCurrent(
  state: DashboardState,
  facts: ControlFacts,
  record: IntentView,
): boolean {
  if (record.supersededBySeq !== null) return false;
  const newest = state.highestRevision;
  switch (record.kind) {
    case 'turn_on': {
      // S-013, D-10: a receipt older than a newer choice is history, whatever arrived first.
      if (record.answer === null || newest === null) return true;
      const answer = classifyHarvestAnswer(record.answer, 'turn_on');
      return answer.kind !== 'turned_on' || newest <= answer.revision;
    }
    case 'pause': {
      if (record.answer === null || newest === null) return true;
      const answer = classifyHarvestAnswer(record.answer, 'pause');
      return answer.kind !== 'paused' || newest <= answer.revision;
    }
    case 'input_off':
      // S-047: the whole turn-off outcome is history once any newer input fact exists.
      return !(
        facts.input.turnOff !== null &&
        facts.input.turnOff.answerSeq === record.answerSeq &&
        turnOffSuperseded(facts.input)
      );
    default:
      return true;
  }
}

/** S-046: the input answer's "harvesting paused" claim, current only until a read shows a newer choice. */
export function harvestPausedCurrent(state: DashboardState, record: IntentView): boolean | null {
  if (record.kind !== 'input_off' || record.answer === null) return null;
  const answer = classifyInputAnswer(record.answer, 'disable');
  const claim =
    answer.kind === 'stopping' ||
    answer.kind === 'stopping_with_errors' ||
    answer.kind === 'revocation_failed'
      ? answer.harvestPaused
      : null;
  if (claim === null) return null;
  const newest = state.highestRevision;
  if (newest === null) return true;
  return claim.revision !== null && newest <= claim.revision;
}
