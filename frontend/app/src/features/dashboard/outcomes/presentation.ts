import { copy } from '../../../copy/catalogue.ts';
import type { CopyEntry } from '../../../copy/text.ts';
import type { OutcomeLineVM } from '../../../store/selectors/outcomes.ts';

const t = copy.dashboard.outcomes;
const lookup = (entries: Readonly<Record<string, CopyEntry>>, code: string, fallback: CopyEntry) =>
  entries[code] ?? fallback;

/** Maps domain codes to catalogue wording; all evidence decisions belong to the selector. */
export function outcomeEntry(line: OutcomeLineVM): CopyEntry {
  if (line.phase === 'sending') return t.sending;
  if (line.kind === 'clear_flag' && (line.detail.kind === 'none' || line.outcome === 'unknown'))
    return t.clearUnknown;
  if (line.settledByRead === 'stopped') return t.stop.readStopped;
  if (line.settledByRead === 'not_applied') return t.harvest.notApplied;
  if (line.settledByRead === 'off_confirmed') return t.harvest.paused;
  if (line.settledByRead === 'recorded' || line.settledByRead === 'applied') return t.recorded;
  const d = line.detail;
  switch (d.kind) {
    case 'stop':
      if (d.class === 'ended')
        return d.endedAttribution === 'this_stop_signalled' ? t.stop.endedSignalled : t.stop.ended;
      if (d.class === 'not_applied') return lookup(t.stop, d.code, t.stop.not_applied);
      if (d.class === 'unverified')
        return lookup(t.stop, d.unverifiedReason ?? '', t.stop.unverified);
      return t.stop[d.class];
    case 'input':
      return line.inputRecoverySettled &&
        (d.class === 'stopping' || d.class === 'stopping_with_errors')
        ? t.input.collectorGone
        : lookup(t.input, d.class, t.input.unknown);
    case 'harvest':
      if (d.class === 'paused') {
        if (d.replayed) return t.harvest.replayed;
        if (d.stopping.length > 0) return t.harvest.stopping;
        if (d.stopped.length > 0) return t.harvest.stopped;
        if (d.noCollectorWasRunning === true) return t.harvest.empty;
      }
      return t.harvest[d.class];
    case 'start':
      return t.start[d.class];
    case 'kill':
      return t.kill[d.class];
    case 'handoff':
      return d.class === 'recorded' ? t.recorded : lookup(t.start, d.class, t.unknown);
    case 'clear_flag':
      return d.class === 'cleared' ? t.cleared : lookup(t.start, d.class, t.unknown);
    case 'run_now':
    case 'reconnect':
    case 'live_controls':
    case 'run_save':
    case 'run_add':
    case 'schedule_save':
    case 'settings_save':
    case 'profile_patch':
    case 'adopt':
    case 'launch':
    case 'prepare':
    case 'tools_install':
    case 'calibrate_start':
    case 'calibrate_observe':
    case 'calibrate_apply':
    case 'calibrate_stop':
    case 'analysis_start':
    case 'template_write':
    case 'account_select':
    case 'account_create':
    case 'account_tier':
    case 'profile_activate':
    case 'profile_copy':
    case 'profile_promote':
      // The Runs and Profiles pages word their own writes; this is the generic receipt.
      return d.class === 'confirmed' || d.class === 'recorded' || d.class === 'saved'
        ? t.recorded
        : lookup(t.start, d.class, t.unknown);
    case 'none':
      return line.kind === 'stop' ? t.stop.unknown : t.unknown;
  }
}
