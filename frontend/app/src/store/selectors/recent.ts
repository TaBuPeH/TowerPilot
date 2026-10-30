import { knownFact, type Fact } from '../../core/facts.ts';
import { lastBattleView, type LastBattleView, type RecordedBattle } from '../../core/lastBattle.ts';
import { finite, own } from '../../core/read.ts';
import { scheduleView, type ScheduleView } from '../../core/schedule.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import { contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState } from '../types.ts';
import { chipVM, type ChipVM } from '../viewModels.ts';

export interface ScheduleVM extends ChipVM {
  readonly winner: ServerText<'name'> | null;
  readonly observedWinner: ServerText<'name'> | null;
  readonly nextClockAt: number | null;
  readonly nextInSec: number | null;
  readonly reason: ServerText<'detail'> | null;
  readonly error: ServerText<'message'> | null;
  readonly previous: ScheduleVM | null;
  readonly clockOffsetSec: number | null;
}

export interface RecordedBattleVM {
  readonly line: RecordedBattle['line'];
  readonly label: ServerText<'name'> | null;
  readonly blueprint: ServerText<'id'> | null;
  readonly status: ServerText<'detail'> | null;
  readonly endReason: ServerText<'message'> | null;
  readonly result: ServerText<'detail'> | null;
  readonly endedAt: number | null;
  readonly wave: number | null;
}

export interface RecentVM {
  readonly schedule: ScheduleVM;
  readonly lastBattle: {
    readonly line: LastBattleView['line'];
    readonly battle: Fact<RecordedBattleVM>;
    readonly readAgeSec: number | null;
    readonly endedAgeSec: number | null;
  };
}

function scheduleVM(view: ScheduleView, elapsedSec = 0, body?: unknown): ScheduleVM {
  const offset = own(body, 'utc_offset');
  const parts = typeof offset === 'string' ? /^([+-])(\d{2})(\d{2})$/u.exec(offset) : null;
  const offsetSec =
    parts === null
      ? null
      : (Number(parts[2]) * 3600 + Number(parts[3]) * 60) * (parts[1] === '-' ? -1 : 1);
  const chip = chipVM(view);
  const age = chip.params['ageSec'];
  return {
    ...chip,
    params:
      view.line === 'last_seen' && typeof age === 'number'
        ? { ...chip.params, ageSec: age + elapsedSec }
        : chip.params,
    winner: fromServer('name', own(view.winner, 'label')),
    observedWinner: fromServer('name', own(view.observedWinner, 'label')),
    nextClockAt: finite(view.nextClockAt) ?? null,
    nextInSec: view.nextInSec === null ? null : Math.max(0, view.nextInSec - elapsedSec),
    reason: fromServer('detail', view.reason),
    error: fromServer('message', view.error),
    previous: view.previous === null ? null : scheduleVM(view.previous, 0, body),
    clockOffsetSec: offsetSec,
  };
}

export function selectRecent(state: DashboardState): RecentVM {
  const schedule = latestRead(state, 'schedule');
  const previousSchedule = state.reads.schedule.lastAnswered;
  const history = latestRead(state, 'battles');
  const previousBattle = state.reads.battles.lastAnswered;
  const last = lastBattleView(
    contextReadOf(history),
    history === null ? NaN : history.arrivedAtMs / 1000,
    previousBattle?.outcome.kind === 'answered'
      ? {
          body: previousBattle.outcome.body,
          sameContext: true,
          observedAt: previousBattle.arrivedAtMs / 1000,
        }
      : null,
  );
  const battle = last.battle;
  const retainedHistory = history?.outcome.kind === 'answered' ? history : previousBattle;
  const historyClock =
    retainedHistory?.outcome.kind === 'answered'
      ? finite(own(retainedHistory.outcome.body, 'server_time'))
      : undefined;
  const historyAge = retainedHistory === null ? null : readAgeSec(state, retainedHistory);
  return {
    schedule: scheduleVM(
      scheduleView(
        contextReadOf(schedule),
        previousSchedule?.outcome.kind === 'answered' && schedule?.outcome.kind === 'failed'
          ? {
              body: previousSchedule.outcome.body,
              sameContext: true,
              ageSec: readAgeSec(state, previousSchedule),
            }
          : null,
      ),
      schedule === null ? 0 : readAgeSec(state, schedule),
      previousSchedule?.outcome.kind === 'answered' ? previousSchedule.outcome.body : undefined,
    ),
    lastBattle: {
      line: last.line,
      readAgeSec: historyAge,
      // The history envelope currently has no server clock. Never compare a
      // recorded server timestamp with the browser clock (spec 7.2).
      endedAgeSec:
        battle.kind === 'known' && battle.value.endedAt !== null && historyClock !== undefined
          ? Math.max(0, historyClock - battle.value.endedAt) + (historyAge ?? 0)
          : null,
      battle:
        battle.kind === 'unknown'
          ? battle
          : knownFact(
              {
                line: battle.value.line,
                label: fromServer('name', battle.value.label),
                blueprint: fromServer('id', battle.value.blueprint),
                status: fromServer('detail', battle.value.status),
                endReason: fromServer('message', battle.value.endReason),
                result: fromServer('detail', battle.value.result),
                endedAt: battle.value.endedAt,
                wave: battle.value.wave,
              },
              battle.observedAt,
            ),
    },
  };
}
