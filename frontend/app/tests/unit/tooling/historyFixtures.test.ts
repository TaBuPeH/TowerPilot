// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { actionsCase, historyAnswer, historyCase } from '../../support/history-fixtures.mjs';
import { loadHistoryFixture } from '../../../scripts/history-fixture.mjs';
import { record } from '../../../scripts/generate-contract.mjs';

const cases = loadHistoryFixture();

describe('shared checkout history and action recordings', () => {
  it.each(['action_executions', 'action_executions_more', 'battle_actions_projection'])(
    '%s returns exact recorded answers as independent deep copies',
    (family) => {
      for (const [name, value] of Object.entries(record(cases[family]))) {
        const row = record(value);
        const answer = actionsCase(`${family}.${name}`);
        expect(answer).toEqual({ status: row['status'], body: row['body'] });
        expect(answer.body).not.toBe(row['body']);
        // Exercise nested objects/arrays as well as the returned body's own members.
        for (const value of Object.values(answer.body)) {
          if (typeof value === 'object' && value !== null)
            for (const key of Object.keys(value)) Reflect.set(value, key, null);
        }
        for (const key of Object.keys(answer.body)) answer.body[key] = null;
        expect(actionsCase(`${family}.${name}`)).toEqual({
          status: row['status'],
          body: row['body'],
        });
      }
    },
  );

  it('mixed replies and history consumers cannot mutate either family of private recordings', () => {
    const path = 'action_executions.battle_detail_of_a_started_battle_lists_its_executions';
    const action = actionsCase(path);
    const history = historyCase('history_detail', 'prepared_nothing_observed');
    const mixed = historyAnswer(new URL('http://fixture/api/battles/example'), 'actions:mixed');
    mixed.body['battle'] = null;
    mixed.body['executions'] = null;
    history.body['battle'] = null;
    expect(actionsCase(path)).toEqual(action);
    expect(historyCase('history_detail', 'prepared_nothing_observed').body).toEqual(
      record(record(cases['history_detail'])['prepared_nothing_observed'])['body'],
    );
    expect(
      historyAnswer(new URL('http://fixture/api/battles/example'), 'actions:mixed').body[
        'executions'
      ],
    ).not.toBeNull();
  });

  it('the continuation still requires the recorded battle and cursor', () => {
    const name = 'battle_detail_truncated_continued_through_the_generic_route';
    const row = record(record(cases['action_executions_more'])[name]);
    const request = record(row['request']);
    const url = new URL(String(request['path']), 'http://fixture');
    expect(historyAnswer(url, 'actions:mixed')).toEqual(
      actionsCase(`action_executions_more.${name}`),
    );
    url.searchParams.set('before', 'wrong-cursor');
    expect(historyAnswer(url, 'actions:mixed').status).toBe(404);
  });
});
