import { afterEach, describe, expect, it, vi } from 'vitest';
import { automationSlot, automationWord } from '../../../src/core/automation.ts';
import { collectionEvidence, runGemReport } from '../../../src/core/collection.ts';
import { confirmCollecting } from '../../../src/core/evidence.ts';
import { pauseOffered } from '../../../src/core/harvestChoice.ts';
import { gemSeen } from '../../../src/core/harvestView.ts';
import { gameInputSeen } from '../../../src/core/input.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { configRead, sampleRecord, serverTimeOf, statusBody } from '../../support/fixture.ts';
import { contextOf, readOf, rec, setPath } from '../../support/core.ts';

afterEach(() => {
  vi.useRealTimers();
});

function collecting(harvest: Record<string, unknown>): boolean {
  const fact = readHarvest(harvest);
  return fact.kind === 'known' && confirmCollecting(fact.value).kind === 'known';
}

const H = serverTimeOf(sampleRecord('status.collecting'));

describe('S-028 running, active, heartbeat or worker_alive alone never prove collection or input', () => {
  it('S-028 a 20 s old publication of a running operation with worker_alive true is not collecting', () => {
    const harvest = sampleRecord('status.collecting');
    const battle = rec(rec(harvest['operation'])['battle']);
    battle['observation_age_sec'] = 20;
    battle['published_at'] = H - 20;
    expect(collecting(harvest)).toBe(false);
    const body = statusBody(harvest);
    expect(gemSeen(contextOf(body)).word).not.toBe('running');
    expect(
      automationWord(
        automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: null }),
      ).word,
    ).not.toBe('running');
  });

  it('S-028 control: the unmodified sample collects', () => {
    expect(collecting(sampleRecord('status.collecting'))).toBe(true);
  });

  it.each(['worker_not_running', 'worker_unverified', 'starting_collector'])(
    'S-028 %s never reads Running for gems, input or automation, with the Setting Allowed or Off',
    (name) => {
      const body = statusBody(sampleRecord(`status.${name}`));
      for (const allowTaps of [true, false]) {
        const input = gameInputSeen({
          config: configRead(allowTaps),
          status: readOf(body),
          enables: [],
          turnOff: null,
          leaseHolders: [],
          retainedHolder: false,
        });
        expect(input.word).not.toBe('running');
      }
      expect(gemSeen(contextOf(body)).word).not.toBe('running');
      expect(
        automationWord(
          automationSlot({ status: contextOf(body), extraClaims: [], lastIdleAgeSec: null }),
        ).word,
      ).not.toBe('running');
    },
  );
});

describe('S-030 green Collecting only with D-6 for the current operation', () => {
  const mutations: [string, (harvest: Record<string, unknown>) => void][] = [
    [
      'observation_age_sec 180',
      (h) => {
        setPath(h, ['operation', 'battle', 'observation_age_sec'], 180);
      },
    ],
    [
      'harvest.server_time removed',
      (h) => {
        setPath(h, ['server_time'], undefined);
      },
    ],
    [
      'published_at H+3600',
      (h) => {
        setPath(h, ['operation', 'battle', 'published_at'], H + 3600);
      },
    ],
    [
      'battle.publication_valid false',
      (h) => {
        setPath(h, ['operation', 'battle', 'publication_valid'], false);
      },
    ],
    [
      'battle.id an object',
      (h) => {
        setPath(h, ['operation', 'battle', 'id'], {});
      },
    ],
    [
      "evidence.generation 'g2'",
      (h) => {
        setPath(h, ['operation', 'evidence', 'generation'], 'g2');
      },
    ],
    [
      'routines []',
      (h) => {
        setPath(h, ['operation', 'routines'], []);
      },
    ],
    [
      'routines {}',
      (h) => {
        setPath(h, ['operation', 'routines'], {});
      },
    ],
    [
      'routines removed',
      (h) => {
        setPath(h, ['operation', 'routines'], undefined);
      },
    ],
    [
      'both gem routines enabled false',
      (h) => {
        setPath(h, ['operation', 'routines', 'ad_gems', 'enabled'], false);
        setPath(h, ['operation', 'routines', 'floating_gems', 'enabled'], false);
      },
    ],
    [
      'ad_gems.enabled "true"',
      (h) => {
        setPath(h, ['operation', 'routines', 'ad_gems', 'enabled'], 'true');
      },
    ],
    [
      'input_revoked "true"',
      (h) => {
        setPath(h, ['input_revoked'], 'true');
      },
    ],
    [
      'input_revoked {}',
      (h) => {
        setPath(h, ['input_revoked'], {});
      },
    ],
    [
      'worker_alive null',
      (h) => {
        setPath(h, ['operation', 'worker_alive'], null);
      },
    ],
    [
      'worker_alive false',
      (h) => {
        setPath(h, ['operation', 'worker_alive'], false);
      },
    ],
    [
      'battle.fresh false',
      (h) => {
        setPath(h, ['operation', 'battle', 'fresh'], false);
      },
    ],
    [
      'an evidence time after H+1',
      (h) => {
        setPath(h, ['operation', 'evidence', 'observed_through_at'], H + 5);
      },
    ],
  ];

  it.each(mutations)('S-030 %s is never green and keeps Pause', (_label, mutate) => {
    const harvest = sampleRecord('status.collecting');
    mutate(harvest);
    expect(collecting(harvest)).toBe(false);
    const body = statusBody(harvest);
    const seen = gemSeen(contextOf(body));
    expect(seen.row).not.toBe('G3a');
    expect(pauseOffered(readOf(body).harvest, null, [])).toBe(true);
  });

  it('S-030 the published code gem_routines_paused on a valid record reads live paused (G3b)', () => {
    const harvest = sampleRecord('status.collecting');
    setPath(harvest, ['operation', 'routines', 'ad_gems', 'enabled'], false);
    setPath(harvest, ['operation', 'routines', 'floating_gems', 'enabled'], false);
    setPath(harvest, ['code'], 'gem_routines_paused');
    expect(gemSeen(contextOf(statusBody(harvest)))).toMatchObject({
      row: 'G3b',
      word: 'paused',
      evidence: 'EV-2',
    });
    expect(gemSeen(contextOf(statusBody(sampleRecord('status.live_paused'))))).toMatchObject({
      row: 'G3b',
    });
  });

  it('S-030 the page reads the published code: routines that read off under a collecting code are not re-classified as paused', () => {
    const harvest = sampleRecord('status.collecting');
    setPath(harvest, ['operation', 'routines', 'ad_gems', 'enabled'], false);
    setPath(harvest, ['operation', 'routines', 'floating_gems', 'enabled'], false);
    const seen = gemSeen(contextOf(statusBody(harvest)));
    expect(seen.row).not.toBe('G3b');
    expect(seen.row).not.toBe('G3a');
  });

  it('S-030 the stale collector sample reads collector state unknown', () => {
    expect(gemSeen(contextOf(statusBody(sampleRecord('status.stale_collector'))))).toMatchObject({
      row: 'G3f',
      word: 'unknown',
    });
  });

  it('S-030 control: the unmodified sample is green, and only floating_gems true is green too', () => {
    expect(collecting(sampleRecord('status.collecting'))).toBe(true);
    const floating = sampleRecord('status.collecting');
    setPath(floating, ['operation', 'routines', 'ad_gems', 'enabled'], false);
    setPath(floating, ['operation', 'routines', 'floating_gems', 'enabled'], true);
    expect(collecting(floating)).toBe(true);
    const fact = readHarvest(floating);
    expect(fact.kind === 'known' && collectionEvidence(fact.value)).toMatchObject({
      kind: 'known',
      value: { adGems: false, floatingGems: true },
    });
  });

  it.each([-86_400_000, 86_400_000])(
    'S-030 a browser clock shifted by %i ms changes nothing',
    (shift) => {
      vi.useFakeTimers();
      vi.setSystemTime(H * 1000 + shift);
      expect(collecting(sampleRecord('status.collecting'))).toBe(true);
      const stale = sampleRecord('status.collecting');
      setPath(stale, ['operation', 'battle', 'observation_age_sec'], 180);
      expect(collecting(stale)).toBe(false);
    },
  );
});

describe("S-035 a full driver's gem claim is collecting only with gathers, published_routines and a true flag", () => {
  function report(name: string): string {
    const fact = readHarvest(sampleRecord(`status.${name}`));
    return fact.kind === 'known' ? runGemReport(fact.value).kind : 'unreadable';
  }

  it('S-035 owned_by_run_gathers: the run is collecting gems (G4a, EV-4)', () => {
    expect(report('owned_by_run_gathers')).toBe('known');
    expect(
      gemSeen(contextOf(statusBody(sampleRecord('status.owned_by_run_gathers')))),
    ).toMatchObject({
      row: 'G4a',
      word: 'running',
      evidence: 'EV-4',
    });
  });

  it('S-035 owned_by_run_not_gathering: the run is not collecting gems (G4b)', () => {
    expect(report('owned_by_run_not_gathering')).toBe('unknown');
    expect(
      gemSeen(contextOf(statusBody(sampleRecord('status.owned_by_run_not_gathering')))),
    ).toMatchObject({
      row: 'G4b',
      word: 'waiting',
    });
  });

  it.each(['owned_by_run_not_reported', 'owned_by_run_stale'])(
    'S-035 %s is unknown (G4d)',
    (name) => {
      expect(report(name)).toBe('unknown');
      expect(gemSeen(contextOf(statusBody(sampleRecord(`status.${name}`))))).toMatchObject({
        row: 'G4d',
        word: 'unknown',
      });
    },
  );

  it('S-035 a gem flag that is not exactly true, or kind run alone, never implies gathering', () => {
    const harvest = sampleRecord('status.owned_by_run_gathers');
    setPath(harvest, ['owner', 'gems', 'ad_gems'], 'true');
    const fact = readHarvest(harvest);
    expect(fact.kind === 'known' && runGemReport(fact.value).kind).toBe('unknown');
    const gathersMissing = sampleRecord('status.owned_by_run_gathers');
    setPath(gathersMissing, ['owner', 'gathers'], undefined);
    const missing = readHarvest(gathersMissing);
    expect(missing.kind === 'known' && runGemReport(missing.value).kind).toBe('unknown');
  });

  it.each([
    'owned_by_run_gathers',
    'owned_by_run_not_gathering',
    'owned_by_run_not_reported',
    'owned_by_run_stale',
  ])('S-035 control: %s never reads as standalone Collecting', (name) => {
    expect(collecting(sampleRecord(`status.${name}`))).toBe(false);
    expect(gemSeen(contextOf(statusBody(sampleRecord(`status.${name}`)))).row).not.toBe('G3a');
  });
});
