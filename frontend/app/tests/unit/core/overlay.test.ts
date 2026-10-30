import { describe, expect, it } from 'vitest';
import { overlayView } from '../../../src/core/overlay.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import { sample, sampleNames, sampleRecord } from '../../support/fixture.ts';

function view(raw: unknown) {
  const harvest = readHarvest(raw);
  if (harvest.kind !== 'known') throw new Error('readable harvest required');
  return overlayView(harvest.value);
}
describe('overlay facts', () => {
  it.each(sampleNames('status').filter((name) => name.startsWith('overlay_')))(
    'S-036 reports per-routine targets, skipped reasons and battle timing for %s',
    (name) => {
      const raw = sample(`status.${name}`);
      const result = view(raw);
      expect(result.kind).toBe('reported');
      expect(result.state).toBe(ownPath(raw, 'overlay', 'state'));
      expect(result.cadence).toBe('current_battle');
      expect(result.applied).toBe(name === 'overlay_applied');
      expect(result.pending).toBe(
        ['overlay_requested', 'overlay_waiting', 'overlay_off_pending'].includes(name),
      );
      for (const routine of result.routines) {
        expect(routine.target).toBe(ownPath(raw, 'overlay', 'targets', routine.routine));
        expect(routine.skipped).toBe(ownPath(raw, 'overlay', 'skipped', routine.routine) ?? null);
      }
      expect(Object.keys(result)).not.toContain('interval_sec');
      expect(JSON.stringify(result)).not.toContain('request_key');
    },
  );
  it('S-036 absent overlay claims no applied or source cadence', () => {
    expect(view(sample('status.off'))).toMatchObject({
      kind: 'none',
      applied: false,
      pending: false,
      cadence: 'unknown',
    });
  });
  it('S-036 unsupported, live choice and unchanged remain distinct per routine', () => {
    expect(view(sample('status.overlay_live_choice')).routines).toEqual([
      { routine: 'ad_gems', target: true, skipped: 'live_choice' },
      { routine: 'floating_gems', target: false, skipped: 'unchanged' },
    ]);
    expect(
      view(sample('status.overlay_unsupported')).routines.every(
        (routine) => routine.skipped === 'unsupported',
      ),
    ).toBe(true);
  });
  it('S-036 requested, waiting and Off pending never claim applied', () => {
    const raw = sampleRecord('status.overlay_applied');
    for (const state of ['requested', 'waiting', 'off_pending']) {
      raw['overlay'] = { ...(own(raw, 'overlay') as Record<string, unknown>), state };
      expect(view(raw)).toMatchObject({ pending: true, applied: false });
    }
  });
  it('S-095 malformed overlay fields do not coerce values or inherit routine names', () => {
    const raw = sampleRecord('status.overlay_applied');
    const targets: unknown = Object.create({ ad_gems: true });
    raw['overlay'] = {
      state: { toString: null },
      targets,
      skipped: { floating_gems: {} },
      cadence_source: {},
    };
    expect(view(raw)).toMatchObject({
      applied: false,
      cadence: 'unknown',
      routines: [
        { routine: 'ad_gems', target: null },
        { routine: 'floating_gems', skipped: 'unknown' },
      ],
    });
  });
});
