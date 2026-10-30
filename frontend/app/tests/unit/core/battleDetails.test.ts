import { describe, expect, it } from 'vitest';
import { BATTLE_REPORT_MAX_AGE_SEC, battleServerNow } from '../../../src/core/battle.ts';
import { ownPath } from '../../../src/core/read.ts';
import { adGemsDetails, floatingRefusal, routineEventsQuery } from '../../../src/core/routines.ts';
import { dashboardBody, dashboardPagesCase } from '../../support/dashboardFixture.ts';

/**
 * F6-11 RL7 (backend/logs/reviews/ui-pages/f6-11-runs-leftovers-brief.md, spec F-062, F-063):
 * the battle details the Runs live section shows, from the recorded battle reads
 * (dashboard_contract_v1.json battles_active, dashboard_contract_pages_v1.json run_controls_live).
 */
const RECORDED = dashboardBody('battles_active.runtime_ad_orbit_shopping');
const battleOf = (body: unknown) => ownPath(body, 'battle');
const routine = (body: unknown, name: string): Record<string, unknown> => {
  const found = ownPath(body, 'battle', 'runtime', 'routines', name);
  if (typeof found !== 'object' || found === null) throw new Error(`no ${name} routine`);
  return { ...(found as Record<string, unknown>) };
};
const live = (name: string) => dashboardPagesCase(`run_controls_live.${name}`).body;
const NOW = 1_800_000_002;

/** The recorded seen offer whose claim failed (r12 candidate battle_details_runtime). */
const SEEN = dashboardPagesCase(
  'battle_details_runtime.ad_gems_offer_seen_and_failed_attempt',
).body;

describe('F6-11 RL7 Ad gems details', () => {
  it('RL7 the recorded report: offer not observed, window open, counters and timing as sent', () => {
    expect(adGemsDetails(routine(RECORDED, 'ad_gems'), NOW)).toEqual({
      visibility: 'unknown',
      visibilityReason: 'not_observed',
      observationStale: false,
      firstSeenAt: null,
      appearance: null,
      lastObservedAt: null,
      collection: { kind: 'window', responseDueAt: null, windowEndsAt: 1_800_000_020 },
      nextCheckAt: 0,
      failedAttempt: null,
      counters: { attempts: 0, confirmed: 1, skips: 0, windowsMissed: 0 },
      durations: { schedulingWaitSec: null, responseDelaySec: null, verificationSec: 2 },
    });
  });

  it('RL7 a switched-off collector reads "off"; a scheduled one names its next window (recorded)', () => {
    const off = routine(live('read_after_several_changes_overrides_and_applied_values'), 'ad_gems');
    expect(off['enabled']).toBe(false);
    expect(adGemsDetails(off, NOW).collection).toEqual({ kind: 'off' });
    const on = routine(live('read_before_the_first_change'), 'ad_gems');
    expect(adGemsDetails(on, NOW).collection).toEqual({
      kind: 'scheduled',
      nextDueAt: 1_800_000_002,
    });
  });

  // Variations of the recorded seen offer: each modified copy says what it changes and why.
  function seen(overrides: Record<string, unknown>): Record<string, unknown> {
    return { ...routine(SEEN, 'ad_gems'), ...overrides };
  }

  it('RL7 the recorded seen offer: visible, its appearance bounds, its failed attempt, counters and timing', () => {
    expect(adGemsDetails(routine(SEEN, 'ad_gems'), NOW)).toEqual({
      visibility: 'visible',
      visibilityReason: null,
      observationStale: false,
      firstSeenAt: 1_799_999_991,
      appearance: { afterAt: 1_799_999_986, byAt: 1_799_999_991, gapSec: 5 },
      lastObservedAt: 1_799_999_999,
      collection: { kind: 'scheduled', nextDueAt: 1_800_000_002 },
      nextCheckAt: 0,
      failedAttempt: { reason: 'offer_still_visible', observedAt: 1_800_000_000 },
      counters: { attempts: 1, confirmed: 0, skips: 0, windowsMissed: 0 },
      durations: { schedulingWaitSec: null, responseDelaySec: null, verificationSec: 2 },
    });
  });

  // Modified recorded case: no recorded sighting has a fractional observation interval;
  // the recorded offer's earlier look is moved half a second.
  it('RL7 a fractional appearance interval is kept as reported', () => {
    const details = adGemsDetails(seen({ appearance_after_at: 1_799_999_988.5 }), NOW);
    expect(details.appearance).toEqual({
      afterAt: 1_799_999_988.5,
      byAt: 1_799_999_991,
      gapSec: 2.5,
    });
  });

  // Modified recorded case: the recorded observation made older than its own 8 s limit.
  it("RL7 an observation older than the collector's own limit is not trusted: visibility unknown", () => {
    const details = adGemsDetails(seen({ last_observed_at: NOW - 9 }), NOW);
    expect(details.visibility).toBe('unknown');
    expect(details.observationStale).toBe(true);
    // Without a server clock nothing counts as fresh.
    expect(adGemsDetails(seen({}), null).visibility).toBe('unknown');
  });

  // Modified recorded case: the recorded collector without its own limit.
  it('RL7 without stale_after_sec the battle report limit applies', () => {
    const base = seen({ stale_after_sec: undefined });
    expect(
      adGemsDetails({ ...base, last_observed_at: NOW - BATTLE_REPORT_MAX_AGE_SEC }, NOW).visibility,
    ).toBe('visible');
    expect(
      adGemsDetails({ ...base, last_observed_at: NOW - BATTLE_REPORT_MAX_AGE_SEC - 1 }, NOW)
        .visibility,
    ).toBe('unknown');
  });

  // Modified recorded case: no recorded failure lacks a reason and no recorded collector is
  // switched off with a manual collection pending.
  it('RL7 a failed attempt without a reason names its outcome; a manual collection keeps its timing', () => {
    const details = adGemsDetails(
      seen({
        last_failed_attempt: { outcome: 'window_missed', observed_at: NOW - 4 },
        enabled: false,
        manual_pending: true,
        window_ends_at: NOW + 6,
        response_due_at: NOW + 2,
      }),
      NOW,
    );
    expect(details.failedAttempt).toEqual({ reason: 'window_missed', observedAt: NOW - 4 });
    expect(details.collection).toEqual({
      kind: 'window',
      responseDueAt: NOW + 2,
      windowEndsAt: NOW + 6,
    });
  });
});

describe('F6-11 RL7 Floating gems refusal and the Runtime link', () => {
  it('RL7 the recorded Floating gems report has no refusal', () => {
    expect(floatingRefusal(routine(RECORDED, 'floating_gems'))).toBeNull();
  });

  it('RL7 a recorded refusal is shown with its reason and time', () => {
    const refused = dashboardPagesCase(
      'battle_details_runtime.floating_gems_refusal_recorded',
    ).body;
    expect(floatingRefusal(routine(refused, 'floating_gems'))).toEqual({
      reason: 'invalid_orbit_arc',
      observedAt: 1_800_000_000,
    });
  });

  it("RL7 Inspect events filters Runtime by the battle, its operation and the routine's offer", () => {
    expect(routineEventsQuery(battleOf(RECORDED), routine(RECORDED, 'ad_gems'))).toEqual({
      battle_id: '00000015-0000-4000-8000-000000000000',
      operation_id: '00000016-0000-4000-8000-000000000000',
      offer_id: 'battles_runtime-offer',
    });
    expect(routineEventsQuery(battleOf(RECORDED), routine(RECORDED, 'shopping'))).toEqual({
      battle_id: '00000015-0000-4000-8000-000000000000',
      operation_id: '00000016-0000-4000-8000-000000000000',
    });
  });

  it('RL7 a sighted offer starts the Runtime filter a minute before its first sighting', () => {
    expect(routineEventsQuery(battleOf(SEEN), routine(SEEN, 'ad_gems'))).toEqual({
      battle_id: '0000012a-0000-4000-8000-000000000000',
      operation_id: String(ownPath(SEEN, 'battle', 'operation_id')),
      offer_id: 'bd_ad_offer_failed-offer',
      since: String(1_799_999_991 - 60),
    });
    // Modified recorded case: a bare report sighted in the first minute clamps at zero.
    expect(routineEventsQuery(battleOf(SEEN), { first_seen_at: 30 })['since']).toBe('0');
  });

  it("RL7 the server clock is the read's server_time plus the time since it arrived", () => {
    expect(battleServerNow(RECORDED, 4)).toBe(NOW + 4);
    expect(battleServerNow({}, 4)).toBeNull();
    expect(battleServerNow(RECORDED, -1)).toBeNull();
  });
});
