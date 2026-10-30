// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { createHistoryReader } from '../../../src/api/history.ts';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import {
  actionExecutionsAnswerSchema,
  actionExecutionsMoreAnswerSchema,
  actionsBattleDetailAnswerSchema,
  actionsMoreBattleDetailAnswerSchema,
  actionsProjectionReadAnswerSchema,
} from '../../../src/contract/actions.ts';
import { battleDetailAnswerSchema } from '../../../src/contract/dashboard.ts';
import { historyDetailAnswerSchema } from '../../../src/contract/history.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';

const TARGET = { instance: 'fixture', account: null, serial: 'fixture', epoch: 1 };

async function historyDetail(found: DashboardCase) {
  const text = JSON.stringify(found.body);
  return createHistoryReader(() =>
    Promise.resolve({ kind: 'response', status: found.status, text }),
  ).detail(TARGET, 'fixture', new AbortController().signal);
}

describe('AC6 landed r17b battle_actions_projection', () => {
  it.each(dashboardPagesCaseNames('battle_actions_projection'))(
    'AC6 battle_actions_projection.%s is exact on GET /api/battles/active',
    (name) => {
      const found = dashboardPagesCase(`battle_actions_projection.${name}`);
      expect(found.request.path).toBe('/api/battles/active');
      const answer = { status: found.status, body: found.body };
      expect(actionsProjectionReadAnswerSchema.safeParse(answer).success).toBe(true);
      const parsed = parseAnswer(ENDPOINTS.battlesActive, found.status, JSON.stringify(found.body));
      expect(parsed.contract).toBe('exact');
      expect(parsed.issues).toEqual([]);
      expect(parsed.body).toEqual(found.body);
    },
  );
});

describe('AC6 landed r17b execution history', () => {
  const families = [
    ['action_executions', actionsBattleDetailAnswerSchema, actionExecutionsAnswerSchema],
    [
      'action_executions_more',
      actionsMoreBattleDetailAnswerSchema,
      actionExecutionsMoreAnswerSchema,
    ],
  ] as const;
  for (const [family, detailSchema, listSchema] of families) {
    it.each(dashboardPagesCaseNames(family))(`AC6 ${family}.%s parses exactly`, async (name) => {
      const found = dashboardPagesCase(`${family}.${name}`);
      const answer = { status: found.status, body: found.body };
      if (found.request.path.startsWith('/api/actions/executions')) {
        // The generic execution route has its own strict schema.
        expect(listSchema.safeParse(answer).success).toBe(true);
        return;
      }
      expect(found.request.path.startsWith('/api/battles/')).toBe(true);
      expect(detailSchema.safeParse(answer).success).toBe(true);
      const parsed = parseAnswer(ENDPOINTS.battleDetail, found.status, JSON.stringify(found.body));
      expect(parsed.contract).toBe('exact');
      expect(parsed.issues).toEqual([]);
      expect(parsed.body).toEqual(found.body);
      const history = await historyDetail(found);
      expect(history.kind).toBe('answered');
      expect(history.answer?.contract).toBe('exact');
      expect(history.answer?.body).toEqual(found.body);
    });
  }

  it('AC6 a battle detail with execution rows drifted on the schemas before the actions family', () => {
    const found = dashboardPagesCase(
      'action_executions.battle_detail_of_a_started_battle_lists_its_executions',
    );
    const answer = { status: found.status, body: found.body };
    expect(battleDetailAnswerSchema.safeParse(answer).success).toBe(false);
    expect(historyDetailAnswerSchema.safeParse(answer).success).toBe(false);
  });
});
