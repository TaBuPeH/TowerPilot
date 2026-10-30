import {
  codedAddAnswerSchema,
  codedAddSourceAnswerSchema,
} from '../../../src/contract/r12Families.ts';
import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import {
  runAddAnswer,
  runAddOptions,
  runAddRecorded,
  runIdCheck,
  runRecipes,
} from '../../../src/core/runAdd.ts';
import { entries, own, ownPath } from '../../../src/core/read.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';

const sample = (name: string) => dashboardPagesCase(`run_templates_add.${name}`);
const fenced = (name: string) => dashboardPagesCase(`run_templates_add_fenced.${name}`);
const recipes = runRecipes(sample('read_the_recipes_the_page_offers').body);

describe('RL4 recipe rules', () => {
  it.each(dashboardPagesCaseNames('run_templates_add_coded'))(
    'RL4 stable coded pre-write answer: %s',
    (name) => {
      const entry = dashboardPagesCase(`run_templates_add_coded.${name}`);
      expect(
        parseAnswer(
          entry.request.method === 'POST' ? ENDPOINTS.runTemplatesAdd : ENDPOINTS.profileSource,
          entry.status,
          JSON.stringify(entry.body),
        ).contract,
      ).toBe('exact');
      const schema =
        entry.request.method === 'POST' ? codedAddAnswerSchema : codedAddSourceAnswerSchema;
      expect(schema.safeParse({ status: entry.status, body: entry.body }).success).toBe(true);
      const runId = own(entry.request.body, 'run_id');
      expect(
        runAddAnswer(entry, {
          blueprint: typeof runId === 'string' ? runId : 'new_run',
          preset: 'coin',
        }),
      ).toBe(
        entry.request.method === 'POST' && entry.status === 200
          ? 'confirmed'
          : own(entry.body, 'code') !== undefined
            ? 'refused'
            : 'unconfirmed',
      );
    },
  );
  it.each(['', ' ', 'two', 'NaN', 'Infinity', '1.5'])(
    'RL4 an explicitly entered invalid tier %j stays invalid instead of selecting a default',
    (raw) => {
      const farm = recipes.find((recipe) => recipe.id === 'farm');
      if (farm === undefined) throw new Error('missing farm recipe');
      expect(runAddOptions(farm, { label: 'A run', tier: raw })).toBeNull();
      expect(runAddOptions(farm, { label: 'A run' })).toEqual({ label: 'A run' });
    },
  );
  it('RL4 offers exactly the recorded recipes and their optional policies and kind choices', () => {
    expect(recipes.map((recipe) => recipe.id)).toEqual([
      'farm',
      'tournament',
      'shard',
      'dissonance',
    ]);
    expect(recipes.find((recipe) => recipe.id === 'shard')?.fields).toEqual([
      'label',
      'tier',
      'loadout',
      'count',
    ]);
    expect(recipes.find((recipe) => recipe.id === 'dissonance')?.fields).toContain('perk_bans');
  });

  it.each(
    dashboardPagesCaseNames('run_templates_add').filter((name) => name.startsWith('run_id_')),
  )('RL4 locally refuses the recorded id: %s', (name) => {
    const runId = own(sample(name).request.body, 'run_id');
    if (typeof runId !== 'string') return;
    expect(runIdCheck(runId, sample('read_before_the_first_add').body)).not.toBeNull();
  });

  it.each(['tournament', 'shard', 'dissonance'])(
    'RL4 %s requires an explicit account equipment choice and a label',
    (name) => {
      const recipe = recipes.find((item) => item.id === name);
      if (recipe === undefined) throw new Error('missing recorded recipe');
      expect(runAddOptions(recipe, { label: 'A run' })).toBeNull();
      expect(runAddOptions(recipe, { loadout: 'plain' })).toBeNull();
      expect(runAddOptions(recipe, { label: 'A run', loadout: 'plain' })).toEqual({
        label: 'A run',
        loadout: 'plain',
      });
    },
  );

  it.each(
    dashboardPagesCaseNames('run_templates_add').filter((name) =>
      [400, 409].includes(sample(name).status),
    ),
  )('RL4 F6-11-P2-02 uncoded recorded failure %s is Unknown', (name) => {
    expect(runAddAnswer(sample(name), { blueprint: 'new_run', preset: 'coin' })).toBe(
      'unconfirmed',
    );
  });

  it('RL4 F6-11-P2-02 an ordinary post-write filesystem error stays Unknown regardless of its message', () => {
    const entry = sample('written_then_the_answer_failed');
    // The recorded post-write catch has the same envelope for a backup-cleanup OSError.
    // Change only its message: error prose cannot prove whether the file was written.
    const body = {
      ...Object.fromEntries(entries(entry.body)),
      error: '[Errno 13] Permission denied while removing a profile backup',
    };
    expect(runAddAnswer({ ...entry, body }, { blueprint: 'new_run', preset: 'coin' })).toBe(
      'unconfirmed',
    );
  });

  it.each(
    dashboardPagesCaseNames('run_templates_add_fenced').filter(
      (name) => fenced(name).request.method === 'POST',
    ),
  )('RL4 F6-11-P2-02 guarded answer %s refuses only stable pre-write codes', (name) => {
    const entry = fenced(name);
    const body = entry.request.body;
    const blueprint = own(body, 'run_id');
    const kind = ownPath(
      entry.body,
      'profile',
      'blueprints',
      typeof blueprint === 'string' ? blueprint : '',
      'kind',
    );
    expect(
      runAddAnswer(entry, {
        blueprint: typeof blueprint === 'string' ? blueprint : 'new_run',
        preset: typeof kind === 'string' ? kind : 'coin',
      }),
    ).toBe(
      entry.status === 200
        ? 'confirmed'
        : [
              'current_revision_with_the_same_bad_recipe_is_refused_by_the_recipe',
              'scan_draft_refused_with_the_guards',
              'starter_profile_refused_with_the_guards',
              'the_resend_without_the_revision_is_refused_by_the_run_identifier',
              'unknown_profile_with_the_guards',
              'write_failed_nothing_saved',
              'composed_for_alpha_is_refused_after_the_switch',
              'context_when_config_yaml_is_missing_is_refused_and_creates_nothing',
              'expected_context_missing_the_serial',
              'expected_context_not_an_object',
              'expected_context_null_is_refused_not_read_as_absent',
              'expected_context_serial_not_text',
              'expected_revision_empty_is_a_stale_revision',
              'expected_revision_not_text_is_a_stale_revision',
              'expected_revision_null_is_a_stale_revision',
              'identical_resend_after_the_error_is_refused_stale',
              'resend_of_the_first_request_after_it_committed_is_refused_stale',
              'stale_revision_409_nothing_written',
              'stale_revision_is_judged_before_the_recipe_and_its_choices',
              'the_context_is_judged_before_the_revision',
              'the_current_context_with_a_stale_revision_is_a_revision_conflict',
              'the_identical_resend_is_refused_stale_not_applied_twice',
            ].includes(name)
          ? 'refused'
          : 'unconfirmed',
    );
  });

  it('RL4 a read must hold the sent run with its recipe kind; absence and wrong kind keep Unknown', () => {
    expect(
      runAddRecorded(fenced('read_before_the_first_add').body, {
        blueprint: 'lost_run',
        preset: 'coin',
        baseRevision: String(own(fenced('read_before_the_first_add').body, 'revision')),
      }),
    ).toBe(false);
    expect(
      runAddRecorded(fenced('read_settles_the_lost_answer_the_run_is_there_once').body, {
        blueprint: 'lost_run',
        preset: 'coin',
        baseRevision: String(own(fenced('read_before_the_first_add').body, 'revision')),
      }),
    ).toBe(true);
    expect(
      runAddRecorded(fenced('read_settles_the_lost_answer_the_run_is_there_once').body, {
        blueprint: 'lost_run',
        preset: 'shard',
        baseRevision: String(own(fenced('read_before_the_first_add').body, 'revision')),
      }),
    ).toBe(false);
  });
});

// Fault injection retains the recorded positive profile and varies only revision evidence.
it.each(['missing', 'blank', 'unchanged', 'changed'])(
  'RL4 F6-11-P3-02 %s revision controls read settlement',
  (revisionCase) => {
    const before = dashboardPagesCase('run_templates_add_coded.read_before_any_add');
    const after = dashboardPagesCase(
      'run_templates_add_coded.read_after_an_error_that_followed_the_write_holds_the_run',
    );
    const body = Object.fromEntries(entries(after.body));
    if (revisionCase === 'missing') delete body['revision'];
    if (revisionCase === 'blank') body['revision'] = '';
    if (revisionCase === 'unchanged') body['revision'] = own(before.body, 'revision');
    expect(
      runAddRecorded(body, {
        blueprint: 'half_run',
        preset: 'coin',
        baseRevision: String(own(before.body, 'revision')),
      }),
    ).toBe(revisionCase === 'changed');
  },
);
