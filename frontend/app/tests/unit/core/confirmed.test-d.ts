import { describe, expectTypeOf, it } from 'vitest';
import {
  confirmIdle,
  confirmInputStopped,
  confirmStopped,
  type ClaimState,
  type Confirmed,
} from '../../../src/core/evidence.ts';
import type { Fact } from '../../../src/core/facts.ts';
import type { GateRead, ReadAssessment } from '../../../src/core/gate.ts';
import { decide, observed, type Chip } from '../../../src/core/vocabulary.ts';

/**
 * Type-level proof that a Confirmed value cannot be written outside
 * src/core/evidence.ts, and that its only issuers are the five checked
 * constructors fed by records core itself issued (R7-01). Checked by `vitest`
 * typecheck and `npm run typecheck`: if a line below stopped being an error,
 * its @ts-expect-error would itself fail the check. The brand is type-only
 * (F1, council A-01/A-04): a type assertion is the one way past it, and lint
 * refuses assertions in src/ outside the brand homes (tests/unit/lint/rules.test.ts).
 */
describe('Confirmed', () => {
  it('has no generic issuer: evidence.ts exports only the five checked constructors and readers', () => {
    type Evidence = typeof import('../../../src/core/evidence.ts');
    expectTypeOf<keyof Evidence>().toEqualTypeOf<
      | 'confirmCollecting'
      | 'confirmFree'
      | 'confirmIdle'
      | 'confirmInputStopped'
      | 'confirmStopped'
      | 'readConfirmed'
    >();
  });

  it('confirms only the five claim states', () => {
    expectTypeOf<ClaimState>().toEqualTypeOf<
      'collecting' | 'idle' | 'free' | 'stopped' | 'input_stopped'
    >();
    // @ts-expect-error "running" is never confirmed: no claim of it exists
    expectTypeOf<Confirmed<'running'>>().toBeObject();
  });

  it('is issued only from a gate read core issued, never from a hand-built one', () => {
    const handBuilt = {
      startedSeq: 2,
      harvest: undefined,
      view: { choice: 'off' },
      scan: { ageSec: 0, lastSuccessAt: 1 },
      ownership: { processes: [], operation: null, owner: null },
      serverTime: 1,
    };
    // @ts-expect-error a GateRead carries a brand an object literal cannot have
    confirmIdle(handBuilt, []);
    // @ts-expect-error nor is a hand-built record a GateRead for any other claim
    confirmStopped({ operationId: 'x', pid: null }, handBuilt);
  });

  it('takes the input facts, never a caller verdict on supersession or earlier enables', () => {
    const status: ReadAssessment | null = null;
    confirmInputStopped({ turnOff: null, enables: [], status, config: null });
    confirmInputStopped({
      turnOff: null,
      enables: [],
      status,
      config: null,
      // @ts-expect-error supersession is decided from the facts (S-047), not passed in
      superseded: false,
    });
    // @ts-expect-error a hand-built assessment is not a ReadAssessment
    const forged: ReadAssessment = { startedSeq: 2, answered: true, body: {} };
    expectTypeOf(forged).toEqualTypeOf<ReadAssessment>();
  });

  it('cannot be written as an object literal', () => {
    // @ts-expect-error a claim carries a brand an object literal cannot have
    const forged: Confirmed<'collecting'> = { state: 'collecting', observedAt: 1 };
    expectTypeOf(forged).toEqualTypeOf<Confirmed<'collecting'>>();
  });

  it('cannot be built from a plain value of the same fields', () => {
    const plain = { state: 'idle' as const, observedAt: 1 };
    // @ts-expect-error a plain object lacks the brand
    const forged: Confirmed<'idle'> = plain;
    expectTypeOf(forged).toEqualTypeOf<Confirmed<'idle'>>();
  });

  it('keeps one confirmed state from standing in for another', () => {
    const check = (gate: GateRead) => {
      const fact = confirmStopped({ operationId: 'x', pid: null }, gate);
      expectTypeOf(fact).toEqualTypeOf<Fact<Confirmed<'stopped'>>>();
      if (fact.kind === 'known') {
        // @ts-expect-error a confirmed "stopped" is not a confirmed "collecting"
        const wrong: Confirmed<'collecting'> = fact.value;
        expectTypeOf(wrong).toEqualTypeOf<Confirmed<'collecting'>>();
      }
    };
    expectTypeOf(check).toBeFunction();
  });

  it('A-01 a chip cannot claim a confirmed state without the claim core issued', () => {
    // @ts-expect-error EV-5 (idle) is a confirmed state: decide refuses it without the claim
    decide('G9', 'idle', 'no_gem_collection', 'EV-5');
    // @ts-expect-error nor any other claimed evidence id (EV-3, EV-6, EV-7, EV-8)
    decide('I9', 'idle', 'x', 'EV-7');
    const check = (gate: GateRead) => {
      const idle = confirmIdle(gate, []);
      if (idle.kind === 'known') {
        expectTypeOf(
          observed('G5f', 'idle', 'no_gem_collection', idle.value).claim,
        ).toEqualTypeOf<Confirmed<ClaimState> | null>();
      }
    };
    expectTypeOf(check).toBeFunction();
    // @ts-expect-error a chip cannot be written as an object literal: only core makes one
    const chip: Chip = {
      row: 'G9',
      word: 'idle',
      line: 'x',
      evidence: 'EV-5',
      params: {},
      claim: null,
    };
    expectTypeOf(chip).toEqualTypeOf<Chip>();
  });
});
