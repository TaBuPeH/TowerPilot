import { expect, it } from 'vitest';
import { historyCollection, historyStatsState } from '../../../src/core/history.ts';
import { historyCase } from '../../support/historyFixture.ts';
import { own, record } from '../../../src/core/read.ts';

it.each([
  ['stats_capture_verified', 'verified'],
  ['stats_capture_missing_after_the_battle_ended', 'missing'],
  ['stats_capture_missing_while_the_battle_is_live', 'missing'],
  ['stats_capture_unverified_not_fresh', 'unverified'],
  ['stats_capture_unverified_not_structured', 'unverified'],
  ['stats_capture_unverified_wrong_tier', 'unverified'],
  ['stats_capture_of_an_adopted_battle_stays_unverified', 'unverified'],
  ['stats_capture_failed_no_artifact', 'failed'],
  ['stats_capture_failed_with_artifact', 'failed'],
  ['stats_capture_pending', 'pending'],
] as const)('H3: %s remains %s', (name, expected) => {
  expect(historyStatsState(historyCase('history_detail', name).body['battle'])).toBe(expected);
});

it('H3 F6-HISTORY-01: verification flags never upgrade another capture lifecycle, and a missing/unknown status stays unknown', () => {
  const battle = historyCase('history_detail', 'stats_capture_verified').body['battle'];
  const runtime = own(battle, 'runtime');
  const capture = own(runtime, 'stats_capture');
  for (const [status, expected] of [
    ['failed', 'failed'],
    ['pending', 'pending'],
    ['captured_unverified', 'unverified'],
    [undefined, 'unknown'],
    ['future_capture_state', 'unknown'],
  ] as const) {
    expect(
      historyStatsState({
        ...record(battle),
        runtime: { ...record(runtime), stats_capture: { ...record(capture), status } },
      }),
    ).toBe(expected);
  }
});

it('H3 F6-HISTORY-01: a recorded verified status still needs matching identity and every verification flag', () => {
  const battle = historyCase('history_detail', 'stats_capture_verified').body['battle'];
  const runtime = own(battle, 'runtime');
  const capture = own(runtime, 'stats_capture');
  for (const patch of [
    { verified: false },
    { fresh: false },
    { structured: false },
    { tier_matches: false },
    { wave_consistent: false },
    { battle_id: 'another-record' },
    { envelope_hash: 'another-envelope' },
  ]) {
    expect(
      historyStatsState({
        ...record(battle),
        runtime: { ...record(runtime), stats_capture: { ...record(capture), ...patch } },
      }),
    ).toBe('unverified');
  }
  expect(historyStatsState({ ...record(battle), start_certainty: 'partial_observation' })).toBe(
    'unverified',
  );
});

it('H3: server sampled counters are retained without gem-total arithmetic; wrong generation withholds counts', () => {
  const sampled = historyCollection(
    historyCase('history_detail', 'collection_sampled_confirmations_ended').body['battle'],
  );
  expect(sampled).toMatchObject({ state: 'sampled', claims: 2, balances: 1, increase: 2 });
  const wrong = historyCollection(
    historyCase(
      'history_detail',
      'collection_evidence_of_another_generation_written_through_the_ledger',
    ).body['battle'],
  );
  expect(wrong).toMatchObject({
    state: 'unverified',
    claims: null,
    balances: null,
    increase: null,
  });
});

it('H3: positive counters without the corresponding confirmation remain unverified; genuine zero counters remain zero', () => {
  const battle = historyCase('history_detail', 'collection_sampled_confirmations_ended').body[
    'battle'
  ];
  const runtime = own(battle, 'runtime');
  const evidence = own(runtime, 'collection_evidence');
  for (const latest of [
    {},
    { ad_gems: own(own(evidence, 'latest_confirmations'), 'ad_gems') },
    { floating_gems: own(own(evidence, 'latest_confirmations'), 'floating_gems') },
  ]) {
    const modified = {
      ...record(battle),
      runtime: {
        ...record(runtime),
        collection_evidence: { ...record(evidence), latest_confirmations: latest },
      },
    };
    expect(historyCollection(modified)).toMatchObject({
      state: 'unverified',
      claims: null,
      balances: null,
      increase: null,
    });
  }
  expect(
    historyCollection(historyCase('history_detail', 'collection_none_observed').body['battle']),
  ).toMatchObject({ state: 'sampled', claims: 0, balances: 0, increase: 0 });
});

it.each([
  'collection_confirmation_older_than_the_battle_not_counted',
  'collection_confirmed_with_the_wrong_reason_not_counted',
  'collection_unconfirmed_outcomes_not_counted',
])('H3: %s uses the real server zero counts', (name) => {
  expect(historyCollection(historyCase('history_detail', name).body['battle'])).toMatchObject({
    claims: 0,
    balances: 0,
    increase: 0,
  });
});
