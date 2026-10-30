import { describe, expect, it } from 'vitest';
import {
  applicable,
  applyRecordedIn,
  launchBlock,
  launchRecordedIn,
  operationView,
  stopRecordedIn,
  calibrateStatusView,
  type LaunchFacts,
} from '../../../src/core/recognition.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';

/**
 * Recognition settlement and gates (screens brief R1, R4, R8; compliance F6R10-R1-01,
 * F6R10-R1-05, F6R10-R1-07) over recorded calibrate_writes and recognition_reads cases:
 * a later read settles a lost request only by positive evidence of that request.
 */
const body = (name: string) => dashboardPagesCase(name).body;
const BA = '000000ba-0000-4000-8000-000000000000';
const C2 = '000000c2-0000-4000-8000-000000000000';
const E5 = '000000e5-0000-4000-8000-000000000000';
const E6 = '000000e6-0000-4000-8000-000000000000';

describe('F6R10-R1-07 positive read settlement', () => {
  it('R4 an apply is recorded only by its own operation with the sent revision', () => {
    const applied = body('calibrate_writes.read_the_applied_calibration_by_operation');
    expect(applyRecordedIn(applied, BA, 3)).toBe(true);
    expect(applyRecordedIn(applied, BA, 2)).toBe(false);
    expect(applyRecordedIn(applied, C2, 3)).toBe(false);
    expect(
      applyRecordedIn(body('calibrate_writes.read_the_failed_capture_after_the_apply'), C2, 3),
    ).toBe(true);
    // A read before any apply proves nothing.
    expect(
      applyRecordedIn(body('calibrate_writes.read_the_calibration_after_the_write_failed'), BA, 3),
    ).toBe(false);
  });

  it('R2 R8 a stop is recorded only once its operation moved past the sent revision into stopping', () => {
    const asked = body('calibrate_writes.read_the_calibration_that_was_asked_to_stop');
    expect(stopRecordedIn(asked, E5, 2)).toBe(true);
    expect(stopRecordedIn(asked, E5, 3)).toBe(false);
    expect(stopRecordedIn(asked, E6, 2)).toBe(false);
    // A worker that exited is not a recorded stop.
    expect(
      stopRecordedIn(body('calibrate_writes.read_the_calibration_whose_worker_exited'), E6, 2),
    ).toBe(false);
  });

  it('R1 a lost launch is recorded only by an operation carrying its key', () => {
    const latest = body(
      'calibrate_writes.read_the_latest_calibration_of_the_context_after_a_lost_answer',
    );
    expect(launchRecordedIn(latest, 'k-s1')?.id).toBe('000000b0-0000-4000-8000-000000000000');
    expect(launchRecordedIn(latest, 'k-other')).toBeNull();
  });
});

describe('F6R10-R1-05 what may be applied', () => {
  it.each([
    ['recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence', true],
    [
      'recognition_reads.calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
      true,
    ],
    ['calibrate_writes.read_the_calibration_whose_worker_exited', true],
    ['calibrate_writes.read_the_failed_capture_after_the_apply', false],
    ['calibrate_writes.read_the_applied_calibration_by_operation', false],
    ['recognition_reads.calibrate_status_setup_battle_active_no_final_screen_yet', false],
    ['recognition_reads.calibrate_status_aborted_by_a_person_keeps_the_partial_evidence', false],
  ] as const)('R1 R4 %s may be applied: %s', (name, expected) => {
    const operation = operationView((body(name) as { readonly operation?: unknown }).operation);
    expect(operation).not.toBeNull();
    if (operation !== null) expect(applicable(operation)).toBe(expected);
  });
});

describe('F6R10-R1-01 launches need current facts', () => {
  const idle = calibrateStatusView(body('recognition_reads.calibrate_status_of_the_first_account'));
  const facts: LaunchFacts = {
    contextConfirmed: true,
    launchSending: false,
    launchUnresolved: false,
    status: idle,
    operationWorking: false,
    runner: 'ready',
    battle: false,
  };
  const learn = {
    kind: 'start',
    plan: { kind: 'learn', battle: false, overwrite: false },
  } as const;
  it('R7 R8 an unknown battle or calibrator state refuses a start; capture needs only the calibrator', () => {
    expect(launchBlock(facts, learn)).toBeNull();
    expect(launchBlock({ ...facts, battle: null }, learn)).toBe('battle_not_read');
    expect(launchBlock({ ...facts, battle: null }, { kind: 'observe' })).toBeNull();
    expect(launchBlock({ ...facts, status: null }, learn)).toBe('status_not_read');
    expect(launchBlock({ ...facts, status: null }, { kind: 'observe' })).toBe('status_not_read');
    expect(launchBlock({ ...facts, runner: 'process_list_unavailable' }, { kind: 'observe' })).toBe(
      'process_list_unavailable',
    );
  });
});
