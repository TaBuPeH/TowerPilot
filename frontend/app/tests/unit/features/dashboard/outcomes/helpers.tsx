import { expect, vi } from 'vitest';
import { axe } from 'jest-axe';
import type { RenderResult } from '@testing-library/react';
import { within } from '@testing-library/react';
import { ownPath } from '../../../../../src/core/read.ts';
import type { IntentKindCode } from '../../../../../src/store/types.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { e0Config } from '../../../../support/dataLayer.ts';
import { sampleRecord } from '../../../../support/fixture.ts';
import { answerOf, storeHarness } from '../../../../support/storeHarness.ts';

export function answerState(kind: IntentKindCode, path: string) {
  const h = storeHarness({ config: e0Config(false) });
  const answer = sampleRecord(path);
  const named = ownPath(answer, 'body', 'operation', 'id');
  h.intent({
    id: 'result',
    kind,
    meta:
      kind === 'stop'
        ? {
            operationId: typeof named === 'string' ? named : '00000001-0000-4000-8000-000000000000',
          }
        : kind === 'reconnect'
          ? { serial: 'fake:1' }
          : {},
  });
  h.clock(1000);
  h.answer(
    'result',
    answerOf(answer),
    Number(answer['status']) >= 400
      ? Number(answer['status']) >= 500
        ? 'unconfirmed'
        : 'refused'
      : 'confirmed',
  );
  return h;
}

export async function audit(view: RenderResult) {
  expect(textOutsideCatalogue(view.container)).toEqual([]);
  // axe needs real timers; scenario steps that advance the fake clock finish first.
  if (vi.isFakeTimers()) vi.useRealTimers();
  expect(await axe(view.container)).toHaveNoViolations();
}

/** Scope receipt text away from its separate live announcement. */
export function shown(view: RenderResult) {
  const strip = view.container.querySelector<HTMLElement>('.tp-outcomes');
  if (strip === null) throw new Error('Outcome strip required');
  return within(strip);
}
