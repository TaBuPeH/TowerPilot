import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import { familySchemas } from '../../../src/contract/generated/dashboard_contract_pages_v1_recognition_advanced.39d80e69.ts';
import { entries, own, ownPath, text } from '../../../src/core/read.ts';
import {
  analysisStatus,
  cropCheck,
  dragRect,
  moduleCatalogue,
  requiredImages,
  scanPlan,
  templateInventory,
  validTemplateName,
} from '../../../src/core/recognitionImages.ts';
import { launchAnswer, templateWriteAnswer } from '../../../src/core/recognition.ts';
const fixture = loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture;
const cases = own(fixture, 'cases');
describe('R9 R10 R11 recorded route contracts', () => {
  for (const [descriptor, schema] of Object.entries(familySchemas)) {
    const [family = '', method = '', route = ''] = descriptor.split(' ');
    for (const [name, c] of entries(own(cases, family))) {
      if (
        ownPath(c, 'request', 'method') !== method ||
        !String(ownPath(c, 'request', 'path')).startsWith(route)
      )
        continue;
      it(`R9 R10 R11 strict recorded ${family}.${name}`, () => {
        const body = own(c, 'body');
        expect(schema.safeParse({ status: own(c, 'status'), body }).success).toBe(true);
        if (route === '/api/screen-analysis/status') {
          const operation = text(ownPath(body, 'operation', 'id')) ?? null;
          const view = analysisStatus(body, operation);
          if (operation === null) expect(view.frame).toBeNull();
          else if (
            own(body, 'status') === 'done' &&
            ownPath(body, 'operation', 'status') === 'succeeded'
          ) {
            expect(view.frame).toContain(encodeURIComponent(operation));
            expect(view.matches).toHaveLength((own(body, 'matches') as unknown[]).length);
          } else expect(view.frame).toBeNull();
          expect(analysisStatus(body, 'another-operation').frame).toBeNull();
          expect(analysisStatus(body, 'another-operation').matches).toEqual([]);
          for (const view of [
            analysisStatus(body, 'another-operation'),
            analysisStatus(body, operation, false),
          ]) {
            expect(view.operation).toBeNull();
            expect(view.status).toBeNull();
            expect(view.available).toBe(false);
            expect(view.noMatches).toBe(false);
            expect(view.screen).toBeNull();
            expect(view.candidates).toEqual([]);
            expect(view.tested).toBeNull();
            expect(view.total).toBeNull();
            expect(view.completed).toBeNull();
            expect(view.capturedAt).toBeNull();
            expect(view.size).toBeNull();
            expect(view.frame).toBeNull();
            expect(view.matches).toEqual([]);
          }
        }
        if (route === '/api/screen-analysis/start' && own(c, 'status') === 200)
          expect(launchAnswer({ status: 200, body }).kind).toBe('accepted');
        if (route === '/api/template/' && method === 'POST')
          expect(templateWriteAnswer({ status: Number(own(c, 'status')), body }).kind).toBe(
            own(c, 'status') === 200
              ? 'written'
              : Number(own(c, 'status')) < 500
                ? 'refused'
                : 'unknown',
          );
        if (route === '/api/wizard/templates') {
          const view = templateInventory(body);
          expect(view.have).toEqual(own(body, 'have'));
          expect(view.missingSeen).toEqual(own(body, 'missing_seen'));
        }
        if (route === '/api/wizard/required')
          expect(requiredImages(body).missing).toEqual(own(body, 'missing'));
        if (route === '/api/catalogue/modules')
          expect(moduleCatalogue(body).map((m) => m.learned)).toEqual(
            (own(body, 'modules') as { learned: boolean }[]).map((m) => m.learned),
          );
        if (route === '/api/wizard/scan-plan') {
          const plan = scanPlan(body);
          expect(plan.total).toBe(own(body, 'total'));
          expect(plan.steps.flatMap((s) => s.targets).map((t) => t.status)).toEqual(
            (own(body, 'steps') as { targets: { status: string }[] }[]).flatMap((s) =>
              s.targets.map((t) => t.status),
            ),
          );
        }
      });
    }
  }
});
it('R11 native pixel drag uses display scale in both directions without changing native pixels', () => {
  expect(
    dragRect(
      { x: 25, y: 50 },
      { x: 100, y: 125 },
      { width: 270, height: 640 },
      { width: 1080, height: 2560 },
    ),
  ).toEqual({ x: 100, y: 200, w: 300, h: 300 });
  expect(
    dragRect(
      { x: 100, y: 125 },
      { x: 25, y: 50 },
      { width: 270, height: 640 },
      { width: 1080, height: 2560 },
    ),
  ).toEqual({ x: 100, y: 200, w: 300, h: 300 });
});
it.each([
  '',
  '../a.png',
  'retry.png',
  'buttons/../../a.png',
  'C:/a.png',
  'buttons/x.jpg',
  'buttons/a?x.png',
])('R11 rejects unsafe target %s', (name) => {
  expect(validTemplateName(name)).toBe(false);
});
it.each(['', 'NaN', 'Infinity', '-1', '2.5', ' 2 '])(
  'R11 preserves invalid coordinate %s and never clamps',
  (x) => {
    expect(
      cropCheck(
        { name: 'buttons/retry.png', x, y: '0', w: '10', h: '10' },
        { width: 1080, height: 2560 },
      ),
    ).toEqual({ kind: 'invalid', reason: 'numbers' });
  },
);
it('R10 learned rectangles and per-target verification stay exactly as published', () => {
  const b = ownPath(
    cases,
    'recognition_reads',
    'scan_plan_shows_a_learned_rectangle_and_makes_the_target_automatic',
    'body',
  );
  const target = scanPlan(b)
    .steps.flatMap((s) => s.targets)
    .find((t) => t.rel === 'buttons/retry.png');
  expect(target?.learned).toEqual({ x: 400, y: 2200, w: 280, h: 90 });
  expect(target?.status).toBe('Missing');
  expect(target?.automatic).toBe(true);
});
