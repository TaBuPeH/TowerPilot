import { describe, expect, it } from 'vitest';
import {
  checkEntry,
  entryRaw,
  inactiveEntryRecorded,
  runEntryFields,
  runEntryRuns,
} from '../../../src/core/runEntry.ts';
import {
  profilePatchAnswer,
  sentPatch,
  settleProfilePatch,
} from '../../../src/core/profilePatch.ts';
import { own, stringList } from '../../../src/core/read.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';

const sample = (name: string) => dashboardPagesCase(`run_entry_patch.${name}`);
const initial = sample('read_before_the_first_save').body;
const config = dashboardPagesCase(
  'run_templates_add_fenced.read_config_the_page_context_comes_from',
).body;
const vocab = dashboardPagesCase('profile_reads.vocab_ok').body;
const names = dashboardPagesCaseNames('run_entry_patch');

it('PE6 F6-14c r12f owner allows exactly the recorded inactive entry operations', () => {
  const keys = new Set(
    ['farm', 'dissonance', 'shards', 'tourney', 'quest_sm', 'quest_ilm'].flatMap((run) =>
      runEntryFields(
        dashboardPagesCase('profile_target_more_2.read_before_the_farm_label_is_deleted').body,
        run,
        config,
        vocab,
      ).map((field) => field.key),
    ),
  );
  const writes = ['profile_target', 'profile_target_more', 'profile_target_more_2']
    .flatMap((family) =>
      dashboardPagesCaseNames(family).map((name) => dashboardPagesCase(`${family}.${name}`)),
    )
    .filter(
      (item) =>
        item.request.method === 'POST' &&
        item.request.path === '/api/profile-patch' &&
        item.status === 200 &&
        own(item.request.body, 'name') === 'second' &&
        stringList(own(item.request.body, 'path'))?.[0] === 'blueprints',
    );
  expect(keys.size).toBe(16);
  for (const key of keys)
    for (const remove of [false, true]) {
      const recorded = writes.some(
        (item) =>
          stringList(own(item.request.body, 'path'))?.slice(2).join('.') === key &&
          (own(item.request.body, 'op') === 'delete') === remove,
      );
      expect(inactiveEntryRecorded(key, remove), `${key}, delete=${String(remove)}`).toBe(recorded);
    }
});

describe('RL3 entry domain', () => {
  it('PE6 lists only the target source blueprints with their own labels', () => {
    const source = dashboardPagesCase('profile_target.same_account_inactive_target_source').body;
    expect(runEntryRuns(source)).toEqual([
      { key: 'farm', label: 'Second farm' },
      { key: 'farm_deep', label: 'farm_deep' },
      { key: 'shards', label: 'shards' },
      { key: 'tourney', label: 'tourney' },
    ]);
    expect(runEntryRuns(undefined)).toEqual([]);
  });
  it.each(['farm', 'farm_deep', 'tourney', 'shards', 'dissonance'])(
    'RL3 fields follow the vocabulary for %s',
    (run) => {
      const fields = runEntryFields(initial, run, config, vocab);
      const keys = fields.map((field) => field.key);
      expect(keys).toContain('label');
      expect(keys.includes('gem_entry_max')).toBe(run === 'tourney');
      expect(keys.includes('count')).toBe(run === 'shards');
      expect(keys.includes('dissonant_tab')).toBe(
        ['farm', 'farm_deep', 'dissonance'].includes(run),
      );
      expect(keys.includes('cancel_sprint')).toBe(
        ['farm', 'farm_deep', 'dissonance'].includes(run),
      );
      expect(
        fields.find((field) => field.key === 'loadout')?.choices.map((choice) => choice.value),
      ).toEqual(run === 'shards' || run === 'tourney' ? ['plain'] : ['plain', 'as_is']);
      if (run === 'dissonance')
        expect(
          fields
            .find((field) => field.key === 'dissonant_tab')
            ?.choices.map((choice) => choice.value),
        ).toEqual(['attack', 'defense', 'utility', 'ultimate_weapons']);
    },
  );

  it.each(names.filter((name) => name.startsWith('saved_')))(
    'RL3 parses the recorded field patch %s',
    (name) => {
      const entry = sample(name);
      const path = stringList(own(entry.request.body, 'path')) ?? [];
      const expected = own(entry.request.body, 'expected_revision');
      const previous = names
        .map(sample)
        .find(
          (item) =>
            own(item.body, 'revision') === expected && own(item.body, 'profile') !== undefined,
        );
      // Intermediate GETs are not recorded: use the verbatim preceding write's profile and revision
      // as a GET envelope. This preserves exact saved values while exercising each recorded body.
      const source =
        previous === undefined
          ? initial
          : { profile: own(previous.body, 'profile'), revision: expected };
      const field = runEntryFields(source, path[1] ?? '', config, vocab).find(
        (item) => item.key === path.slice(2).join('.'),
      );
      expect(field).toBeDefined();
      if (field === undefined) throw new Error('missing recorded field');
      const value = own(entry.request.body, 'value');
      const raw =
        typeof value === 'boolean'
          ? value
          : value === null || value === undefined
            ? ''
            : Array.isArray(value)
              ? value.join('\n')
              : typeof value === 'number' || typeof value === 'string'
                ? String(value)
                : '';
      expect(checkEntry(field, raw, own(entry.request.body, 'op') === 'delete')).toEqual({
        kind: 'valid',
        remove: own(entry.request.body, 'op') === 'delete',
        value,
      });
    },
  );

  it.each(['0', '-1', '2.5', '2e3', 'NaN', 'Infinity', '', 'two'])(
    'RL3 preserves invalid tier text %s',
    (raw) => {
      const field = runEntryFields(initial, 'farm', config, vocab).find(
        (item) => item.key === 'tier',
      );
      if (field === undefined) throw new Error('missing tier');
      expect(checkEntry(field, raw).kind).toBe('invalid');
      expect(entryRaw(field)).toBe('1');
    },
  );
  it('RL3 F6-11-P2-06 sends 31 perk bans to the owning server validator', () => {
    const entry = sample('perk_bans_thirty_one_entries');
    const items = stringList(own(entry.request.body, 'value'));
    const field = runEntryFields(initial, 'farm', config, vocab).find(
      (item) => item.key === 'perk_bans',
    );
    if (field === undefined || items === undefined) throw new Error('missing recorded perk bans');
    expect(items).toHaveLength(31);
    expect(checkEntry(field, items.join('\n'))).toEqual({
      kind: 'valid',
      remove: false,
      value: items,
    });
  });
  it.each(
    names.filter(
      (name) =>
        sample(name).status === 400 || sample(name).status === 409 || sample(name).status === 404,
    ),
  )('RL3 classifies recorded refusal %s', (name) => {
    const entry = sample(name);
    expect(profilePatchAnswer(entry)).toMatchObject({
      kind: 'refused',
      status: entry.status,
      error: own(entry.body, 'error'),
    });
  });
  it.each([
    'validator_unavailable_nothing_written_500',
    'write_failed_nothing_saved_500',
    'written_then_the_answer_failed_500',
  ])('RL3 %s stays Unknown until positive recorded readback', (name) => {
    const entry = sample(name);
    expect(profilePatchAnswer(entry)).toEqual({ kind: 'unknown' });
    const sent = sentPatch(entry.request.body);
    if (sent === null) throw new Error('missing patch');
    const read = sample(
      name === 'written_then_the_answer_failed_500'
        ? 'read_after_an_error_that_followed_the_write_holds_the_change'
        : name === 'write_failed_nothing_saved_500'
          ? 'read_after_the_failed_write_the_revision_is_unchanged'
          : 'read_after_the_validator_failure_the_revision_is_unchanged',
    );
    expect(settleProfilePatch(read.body, sent)).toBe(
      name === 'written_then_the_answer_failed_500' ? 'applied' : null,
    );
  });
});
