import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';

/**
 * A2-7 AC4: `GET /api/actions/executions` has its own endpoint id in the owning table, and every
 * recorded 200 answer of the route (the landed r17b families) is exact at the strict level.
 */
const FAMILIES = ['action_executions', 'action_executions_more'] as const;
const ROUTE = '/api/actions/executions';
const answered = FAMILIES.flatMap((family) =>
  dashboardPagesCaseNames(family)
    .map((name) => ({
      path: `${family}.${name}`,
      recorded: dashboardPagesCase(`${family}.${name}`),
    }))
    .filter(({ recorded }) => recorded.status === 200 && recorded.request.path.startsWith(ROUTE)),
);

describe('the generic executions route in the endpoint table', () => {
  it('has its own id, a GET, and its strict schemas', () => {
    expect(ENDPOINTS.actionExecutions).toMatchObject({ id: 'actionExecutions', method: 'GET' });
    expect(ENDPOINTS.actionExecutions.strict.length).toBeGreaterThan(0);
    expect(ENDPOINTS.actionExecutions.members.size).toBeGreaterThan(0);
  });

  it('has recorded answers to read', () => {
    expect(answered.length).toBeGreaterThan(20);
  });

  it.each(answered.map(({ path, recorded }) => [path, recorded] as const))(
    '%s is exact',
    (_path, recorded) => {
      const parsed = parseAnswer(
        ENDPOINTS.actionExecutions,
        recorded.status,
        JSON.stringify(recorded.body),
      );
      expect(parsed.issues).toEqual([]);
      expect(parsed.contract).toBe('exact');
    },
  );
});
