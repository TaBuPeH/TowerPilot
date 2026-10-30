import type { ContextRead } from './connection.ts';
import { finite, own, ownPath, record } from './read.ts';
import { decide, type ChipDecision } from './vocabulary.ts';

export interface PreviousSchedule {
  readonly body: unknown;
  readonly sameContext: boolean;
  readonly ageSec: number;
}

export interface ScheduleView extends ChipDecision {
  readonly winner: unknown;
  readonly observedWinner: unknown;
  readonly nextClockAt: unknown;
  readonly nextInSec: number | null;
  readonly reason: unknown;
  readonly error: unknown;
  readonly previous: ScheduleView | null;
}

/** P1–P11. Eligibility is server-owned; this projection never computes it. */
export function scheduleView(
  read: ContextRead,
  previous: PreviousSchedule | null = null,
): ScheduleView {
  const body = read?.kind === 'answered' && read.sameContext ? read.body : undefined;
  const raw = {
    winner: own(body, 'winner'),
    observedWinner: own(body, 'observed_winner'),
    nextClockAt: ownPath(body, 'next_clock_boundary', 'at'),
    nextInSec: null,
    reason: own(body, 'reason'),
    error: own(body, 'error'),
    previous: null,
  };
  if (read === null) {
    return { ...decide('P1', 'unknown', 'not_checked', null), ...raw };
  }
  if (read.kind !== 'answered' || !read.sameContext) {
    if (previous?.sameContext === true) {
      return {
        ...decide('P2', 'unknown', 'last_known', null, { ageSec: finite(previous.ageSec) ?? null }),
        ...raw,
        previous: scheduleView({ kind: 'answered', body: previous.body, sameContext: true }),
      };
    }
    return { ...decide('P3', 'unknown', 'schedule_unavailable', null), ...raw };
  }
  // backend/control/schedule_status.py read: only available observations carry a winner.
  const serverTime = finite(own(body, 'server_time'));
  if (own(body, 'availability') === 'available') {
    if (record(raw.winner) !== undefined) {
      const next = finite(raw.nextClockAt);
      return {
        ...decide(
          'P4',
          null,
          own(body, 'next_clock_boundary') === null ? 'winner_no_clock_check' : 'winner_next_check',
          'EV-10',
        ),
        ...raw,
        nextInSec:
          next !== undefined && serverTime !== undefined ? Math.max(0, next - serverTime) : null,
      };
    }
    if (
      raw.winner === null &&
      (raw.reason === 'no_eligible_block' || raw.reason === 'no_blocks_today')
    ) {
      return { ...decide('P5', null, raw.reason, 'EV-10'), ...raw };
    }
  }
  switch (raw.reason) {
    case 'plan_unavailable':
      return { ...decide('P6', 'blocked', 'plan_unavailable', null), ...raw };
    case 'scheduler_not_observed':
      return { ...decide('P7', 'unknown', 'not_seen_running', null), ...raw };
    case 'scheduler_not_running': {
      const status = own(body, 'operation_status');
      const decision =
        status === 'cancelling'
          ? decide('P8', 'stopping', 'scheduler_stopping', null)
          : status === 'accepted'
            ? decide('P8', 'waiting', 'scheduler_starting', null)
            : decide('P8', 'unknown', 'scheduler_run_unconfirmed', null);
      return { ...decision, ...raw };
    }
    case 'scheduler_worker_unavailable':
      return { ...decide('P9', 'unknown', 'scheduler_process_unconfirmed', null), ...raw };
    case 'scheduler_observation_stale': {
      const observed = finite(own(body, 'observed_at'));
      const age =
        observed !== undefined && serverTime !== undefined ? serverTime - observed : undefined;
      return {
        ...decide('P10', 'unknown', 'last_seen', null, {
          ageSec: age !== undefined && age >= 0 && Number.isFinite(age) ? age : null,
        }),
        ...raw,
      };
    }
    default:
      return { ...decide('P11', 'unknown', 'schedule_unknown', null), ...raw };
  }
}
