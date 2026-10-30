import { describe, expect, it } from 'vitest';
import { gemOutcomes } from '../../../src/core/collection.ts';
import { gemSeen, gemSlot } from '../../../src/core/harvestView.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, setPath } from '../../support/core.ts';

/**
 * What the gem slot says about this battle's outcomes (council item 6): the
 * state comes from the published collector `code`, the counts and the cause
 * from the backend's `evidence` and `claims`, never recomputed here.
 */
function outcomesOf(name: string, holder: 'operation' | 'owner') {
  const fact = readHarvest(sampleRecord(`status.${name}`));
  if (fact.kind !== 'known') throw new Error(`${name} is not readable`);
  return gemOutcomes(fact.value, holder);
}

const seenOf = (name: string) => gemSeen(contextOf(statusBody(sampleRecord(`status.${name}`))));

describe('the collector state is the backend code', () => {
  it('failing ad claims read as a warning, never as Collecting gems', () => {
    expect(seenOf('working_collector_claims_failing')).toMatchObject({
      row: 'G3g',
      word: 'failed',
      line: 'claims_failing',
      params: { streak: 4 },
    });
  });

  it('the same record with the code collecting is G3a: the row follows the code', () => {
    const harvest = sampleRecord('status.working_collector_claims_failing');
    setPath(harvest, ['code'], 'collecting');
    expect(gemSeen(contextOf(statusBody(harvest)))).toMatchObject({ row: 'G3a', word: 'running' });
  });

  it('a warning that the record cannot prove (stale publication) is unknown, not a stale warning', () => {
    const harvest = sampleRecord('status.working_collector_claims_failing');
    setPath(harvest, ['operation', 'battle', 'fresh'], false);
    expect(gemSeen(contextOf(statusBody(harvest)))).toMatchObject({ row: 'G3f', word: 'unknown' });
  });

  it.each([
    ['collector_stopping', 'G3c'],
    ['harvest_off', 'G3c'],
    ['collector_starting', 'G3d'],
    ['between_battles', 'G3e'],
    ['waiting_for_battle', 'G3e'],
    ['something_new', 'G3f'],
  ])('the published code %s reads %s', (code, row) => {
    const harvest = sampleRecord('status.collecting');
    setPath(harvest, ['code'], code);
    expect(gemSeen(contextOf(statusBody(harvest))).row).toBe(row);
  });
});

describe('confirmations seen, missed windows and the cause of the latest miss', () => {
  it('the collector sample publishes its counts from operation.evidence and claims', () => {
    expect(outcomesOf('collecting', 'operation')).toEqual({
      adConfirmations: 2,
      orbitConfirmations: 0,
      windowsMissed: 1,
      failureStreak: 0,
      cause: { code: 'no_offer_seen', agoSec: 100 },
      finderSuspected: null,
    });
  });

  it('a full run publishes the same counts through its owner view', () => {
    expect(outcomesOf('owned_by_run_gathers', 'owner')).toMatchObject({
      adConfirmations: 2,
      orbitConfirmations: 0,
      windowsMissed: 2,
      cause: { code: 'window_ran_out' },
    });
    expect(
      gemSlot(contextOf(statusBody(sampleRecord('status.owned_by_run_gathers'))), null, [])
        .outcomes,
    ).not.toBeNull();
  });

  it("counts of another battle generation are not this battle's", () => {
    const harvest = sampleRecord('status.owned_by_run_gathers');
    setPath(harvest, ['owner', 'evidence', 'generation'], 'another');
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && gemOutcomes(fact.value, 'owner')).toMatchObject({
      adConfirmations: null,
      orbitConfirmations: null,
      windowsMissed: 2,
    });
    const collector = sampleRecord('status.collecting');
    setPath(collector, ['operation', 'evidence', 'generation'], 'another');
    const read = readHarvest(collector);
    expect(read.kind === 'known' && gemOutcomes(read.value, 'operation')).toBeNull();
  });

  it('an absent or invalid count is null, never zero', () => {
    const harvest = sampleRecord('status.collecting');
    setPath(harvest, ['operation', 'evidence', 'ad_claim_confirmations'], -1);
    setPath(harvest, ['operation', 'claims', 'windows_missed'], null);
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && gemOutcomes(fact.value, 'operation')).toMatchObject({
      adConfirmations: null,
      orbitConfirmations: 0,
      windowsMissed: null,
    });
  });

  it.each([
    [true, true],
    [false, false],
    [null, null],
    ['yes', null],
    [undefined, null],
  ])('claims.finder_suspected %s is read as %s, never computed', (published, read) => {
    const harvest = sampleRecord('status.collecting');
    setPath(harvest, ['operation', 'claims', 'finder_suspected'], published);
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && gemOutcomes(fact.value, 'operation')?.finderSuspected).toBe(
      read,
    );
  });

  it('a full run reads finder_suspected from its owner claims', () => {
    const harvest = sampleRecord('status.owned_by_run_gathers');
    setPath(harvest, ['owner', 'claims', 'finder_suspected'], true);
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && gemOutcomes(fact.value, 'owner')?.finderSuspected).toBe(true);
  });

  it('a stale publication shows no outcomes', () => {
    expect(outcomesOf('stale_collector', 'operation')).toBeNull();
    expect(
      gemSlot(contextOf(statusBody(sampleRecord('status.stale_collector'))), null, []).outcomes,
    ).toBeNull();
  });

  it('only the rows that say gems are being handled carry outcomes', () => {
    for (const name of ['off', 'live_paused', 'waiting_for_battle', 'backing_off']) {
      expect(
        gemSlot(contextOf(statusBody(sampleRecord(`status.${name}`))), null, []).outcomes,
        name,
      ).toBeNull();
    }
  });
});
