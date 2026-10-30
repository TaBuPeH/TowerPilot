import { classifyHarvestAnswer } from '../../core/outcomes/harvest.ts';
import { classifyInputAnswer } from '../../core/outcomes/input.ts';
import { classifyKillAnswer } from '../../core/outcomes/kill.ts';
import {
  classifyClearFlagAnswer,
  classifyHandoffAnswer,
  classifyStartAnswer,
} from '../../core/outcomes/start.ts';
import { classifyStopAnswer } from '../../core/outcomes/stop.ts';
import { own } from '../../core/read.ts';
import { runAddAnswer } from '../../core/runAdd.ts';
import { commandAnswer, runNowAnswer } from '../../core/routines.ts';
import { profileWriteAnswer } from '../../core/profiles.ts';
import { savedSaveAnswer } from '../../core/runControls.ts';
import { profilePatchAnswer } from '../../core/profilePatch.ts';
import { settingsSaveAnswer } from '../../core/settingsFields.ts';
import { adoptAnswer, installAnswer, launchAnswer, prepareAnswer } from '../../core/emulators.ts';
import {
  applyAnswer,
  calibrateStopAnswer,
  launchAnswer as operationLaunchAnswer,
  templateWriteAnswer,
} from '../../core/recognition.ts';
import type { IntentAnswer, IntentKind, IntentMeta, SettledPhase } from './types.ts';

/**
 * How an answer settles its record, decided by core's answer classifiers
 * (spec 7.4 last column, 7.5): `confirmed` when the server applied the request
 * (its own state words come from the classifier, never from this phase),
 * `refused` when it answered that nothing was applied, `unconfirmed` when the
 * outcome is unknown (a 5xx, `harvest_outcome_unknown`, an answer outside the
 * contract shape). An unknown answer is never read as a refusal or a success.
 */
export function settledPhase(
  kind: IntentKind,
  answer: IntentAnswer,
  meta: IntentMeta,
): SettledPhase {
  const raw = { status: answer.status, body: answer.body };
  switch (kind) {
    case 'run_add':
      return runAddAnswer(raw, meta);
    case 'schedule_save':
    case 'profile_patch': {
      // POST /api/profile-patch: one owner of its answer rules (core/profilePatch.ts).
      const outcome = profilePatchAnswer(raw);
      return outcome.kind === 'done'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'adopt': {
      // Saved is confirmed even when its own connect attempt failed (shown separately).
      const outcome = adoptAnswer(raw, meta);
      return outcome.kind === 'saved'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'launch': {
      // An HTTP 200 with ok:false is the manager's own failure: answered, nothing started.
      const outcome = launchAnswer(raw);
      return outcome.kind === 'accepted'
        ? 'confirmed'
        : outcome.kind === 'failed' || outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'prepare': {
      const outcome = prepareAnswer(raw);
      return outcome.kind === 'done'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'tools_install': {
      const outcome = installAnswer(raw);
      return outcome.kind === 'accepted'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'calibrate_start':
    case 'calibrate_observe':
    case 'analysis_start': {
      // Recognition (screens brief R1, R5, R9): a C5 receipt names the operation.
      const outcome = operationLaunchAnswer(raw);
      return outcome.kind === 'accepted'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'calibrate_apply': {
      // A 400 apply_failed returned the operation to awaiting_apply; a 500 is unknown.
      const outcome = applyAnswer(raw);
      return outcome.kind === 'applied'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'calibrate_stop': {
      const outcome = calibrateStopAnswer(raw);
      return outcome.kind === 'answered'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'template_write': {
      // A written file proves neither its provenance nor its recognition (shown separately).
      const outcome = templateWriteAnswer(raw);
      return outcome.kind === 'written'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'settings_save': {
      const outcome = settingsSaveAnswer(raw, meta);
      return outcome.kind === 'saved'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'pause': {
      const outcome = classifyHarvestAnswer(raw, 'pause');
      return outcome.kind === 'paused'
        ? 'confirmed'
        : outcome.kind === 'not_applied'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'turn_on': {
      const outcome = classifyHarvestAnswer(raw, 'turn_on');
      return outcome.kind === 'turned_on'
        ? 'confirmed'
        : outcome.kind === 'not_applied'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'input_off': {
      const outcome = classifyInputAnswer(raw, 'disable');
      switch (outcome.kind) {
        case 'unknown':
          return 'unconfirmed';
        case 'revocation_failed':
          // S-043: a definite answer that input is NOT revoked (with what was done listed).
          return 'refused';
        default:
          return 'confirmed';
      }
    }
    case 'input_on': {
      const outcome = classifyInputAnswer(raw, 'enable');
      return outcome.kind === 'enabled'
        ? 'confirmed'
        : outcome.kind === 'enable_refused' || outcome.kind === 'enable_refused_owner'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'stop': {
      if (meta.operationId === undefined) return 'unconfirmed';
      const outcome = classifyStopAnswer(raw, meta.operationId);
      return outcome.kind === 'unknown'
        ? 'unconfirmed'
        : outcome.kind === 'not_applied' || outcome.kind === 'action_lock'
          ? 'refused'
          : 'confirmed';
    }
    case 'kill': {
      const outcome = classifyKillAnswer(raw);
      return outcome.kind === 'no_longer_running'
        ? 'confirmed'
        : outcome.kind === 'unknown'
          ? 'unconfirmed'
          : 'refused';
    }
    case 'handoff': {
      const outcome = classifyHandoffAnswer(raw);
      return outcome.kind === 'recorded'
        ? 'confirmed'
        : outcome.kind === 'unknown'
          ? 'unconfirmed'
          : 'refused';
    }
    case 'start':
    case 'start_gems': {
      const outcome = classifyStartAnswer(raw);
      switch (outcome.kind) {
        case 'unknown':
          return 'unconfirmed';
        case 'started':
        case 'failed':
        case 'cancelled':
        case 'interrupted':
        case 'succeeded':
          return 'confirmed';
        default:
          return 'refused';
      }
    }
    case 'clear_flag': {
      const outcome = classifyClearFlagAnswer(raw);
      return outcome.kind === 'cleared'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'reconnect':
      // POST /api/wizard/reconnect: 200 {ok, device_state, message}; 409 owner refusal; 500 {ok:false, error}.
      if (answer.status === 200 && own(answer.body, 'ok') === true) return 'confirmed';
      return answer.status >= 400 && answer.status < 500 ? 'refused' : 'unconfirmed';
    case 'run_now': {
      const outcome = runNowAnswer({ kind: 'answered', status: answer.status, body: answer.body });
      return outcome.kind === 'recorded'
        ? 'confirmed'
        : outcome.kind === 'not_recorded'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'live_controls': {
      const outcome = commandAnswer(
        { kind: 'answered', status: answer.status, body: answer.body },
        'controls',
      );
      return outcome.kind === 'recorded'
        ? 'confirmed'
        : outcome.kind === 'not_recorded'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'account_select':
    case 'account_create':
    case 'account_tier':
    case 'profile_activate':
    case 'profile_copy':
    case 'profile_promote': {
      const outcome = profileWriteAnswer(kind, raw, meta);
      return outcome.kind === 'done'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
    case 'run_save': {
      const outcome = savedSaveAnswer(raw, meta);
      return outcome.kind === 'saved' || outcome.kind === 'saved_partly'
        ? 'confirmed'
        : outcome.kind === 'refused'
          ? 'refused'
          : 'unconfirmed';
    }
  }
}
