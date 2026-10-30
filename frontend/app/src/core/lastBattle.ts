import type { ContextRead } from './connection.ts';
import { knownFact, unknownFact, type Fact } from './facts.ts';
import { finite, list, own, record } from './read.ts';

export interface RecordedBattle {
  readonly line: 'ended' | 'tracking_stopped_end_not_seen' | 'tracking_ended';
  readonly label: unknown;
  readonly blueprint: unknown;
  readonly status: unknown;
  readonly endReason: unknown;
  readonly result: unknown;
  readonly endedAt: number | null;
  readonly wave: number | null;
}

export interface PreviousBattle {
  readonly body: unknown;
  readonly sameContext: boolean;
  readonly observedAt: number;
}

export interface LastBattleView {
  readonly line:
    | 'not_checked'
    | 'unavailable'
    | 'last_known'
    | 'no_battle_recorded'
    | 'recorded'
    | 'no_previous_battle';
  readonly battle: Fact<RecordedBattle>;
}

/** F-079/F-080: a past record is a Fact, never evidence of the game now. */
export function lastBattleView(
  read: ContextRead,
  observedAt: number,
  previous: PreviousBattle | null = null,
): LastBattleView {
  if (read === null) return { line: 'not_checked', battle: unknownFact('not_checked') };
  if (read.kind !== 'answered' || !read.sameContext) {
    if (previous?.sameContext === true) {
      const last = lastBattleView(
        { kind: 'answered', sameContext: true, body: previous.body },
        previous.observedAt,
      );
      if (last.battle.kind === 'known') return { line: 'last_known', battle: last.battle };
    }
    return { line: 'unavailable', battle: unknownFact('history_unavailable') };
  }
  // backend/dashboard.py api_battle_history -> control/battles.py public.
  const rows = list(own(read.body, 'battles'));
  if (rows === undefined) return { line: 'unavailable', battle: unknownFact('battles_invalid') };
  if (rows.length === 0) return { line: 'no_battle_recorded', battle: unknownFact('no_record') };
  for (const row of rows) {
    if (record(row) === undefined)
      return { line: 'unavailable', battle: unknownFact('battle_invalid') };
    const status = own(row, 'status');
    if (status === 'active' || status === 'prepared') continue;
    const finalWave = finite(own(row, 'final_wave'));
    const wave = finalWave ?? finite(own(row, 'wave'));
    return {
      line: 'recorded',
      battle: knownFact(
        {
          line:
            status === 'ended'
              ? 'ended'
              : status === 'interrupted'
                ? 'tracking_stopped_end_not_seen'
                : 'tracking_ended',
          label: own(row, 'label'),
          blueprint: own(row, 'blueprint'),
          status,
          endReason: own(row, 'end_reason'),
          result: own(row, 'result'),
          endedAt: finite(own(row, 'ended_at')) ?? finite(own(row, 'updated_at')) ?? null,
          wave: wave !== undefined && wave >= 0 ? wave : null,
        },
        observedAt,
      ),
    };
  }
  return { line: 'no_previous_battle', battle: unknownFact('no_non_active_record') };
}
