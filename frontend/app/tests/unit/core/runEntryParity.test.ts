import { describe, expect, it } from 'vitest';
import {
  checkEntry,
  entryNeedsChainLightning,
  runEntryFields,
} from '../../../src/core/runEntry.ts';
import { profilePatchAnswer } from '../../../src/core/profilePatch.ts';
import { entries, own, ownPath } from '../../../src/core/read.ts';
import {
  dashboardPagesCase as recorded,
  dashboardPagesCaseNames,
} from '../../support/dashboardFixture.ts';
import { matchHash, hrefFor } from '../../../src/app/router.ts';

const sample = (name: string) => recorded(`run_entry_patch_parity.${name}`);
const vocab = recorded('profile_reads.vocab_ok').body;
const config = recorded('run_templates_add_fenced.read_config_the_page_context_comes_from').body;
const fields = (source: unknown, run: string) => runEntryFields(source, run, config, vocab);
const field = (source: unknown, run: string, key: string) => {
  const found = fields(source, run).find((item) => item.key === key);
  if (found === undefined) throw new Error(`Missing ${run}.${key}`);
  return found;
};
describe('recorded entry leftovers', () => {
  it('RL11 pre-save ownership reason follows the recorded validator and draft policy', () => {
    const source = recorded(
      'ability_policies.abilities_the_account_does_not_own_are_bound_to_a_run_is_a_problem',
    ).body;
    expect(entryNeedsChainLightning(source, 'farm_cl')).toBe(true);
    expect(entryNeedsChainLightning(source, 'tourney_cl')).toBe(true);
    expect(entryNeedsChainLightning(source, '')).toBe(false);
    // Modified recorded ownership case: the recorded validator's generic ownership
    // predicate also covers the vocabulary's always_on mode. Change only this mode
    // to check the same pre-save notice; no request or backend reply is synthesized.
    const profile = own(source, 'profile'),
      policies = own(profile, 'policies');
    const uw = own(policies, 'uw_policies'),
      policy = own(uw, 'farm_cl');
    const alwaysOn = {
      profile: {
        ...Object.fromEntries(entries(profile)),
        policies: {
          ...Object.fromEntries(entries(policies)),
          uw_policies: {
            ...Object.fromEntries(entries(uw)),
            farm_cl: {
              ...Object.fromEntries(entries(policy)),
              chain_lightning: {
                ...Object.fromEntries(entries(ownPath(policy, 'chain_lightning'))),
                mode: 'always_on',
              },
            },
          },
        },
      },
    };
    expect(entryNeedsChainLightning(alwaysOn, 'farm_cl')).toBe(true);
    expect(String(own(source, 'problems'))).toContain(
      'needs Chain Lightning, which the player does not own',
    );
  });
  for (const family of ['run_entry_patch_parity', 'run_kind_guard', 'plan_removal_effects']) {
    it.each(
      dashboardPagesCaseNames(family).filter(
        (name) => recorded(`${family}.${name}`).status === 400,
      ),
    )(`RL8 RL9 RL12 RL13 ${family} recorded refusal remains verbatim: %s`, (name) => {
      const entry = recorded(`${family}.${name}`);
      expect(profilePatchAnswer(entry)).toMatchObject({
        kind: 'refused',
        error: own(entry.body, 'error'),
      });
    });
  }
  it('RL9 explicit label deletion includes an absent label; blanks never save', () => {
    const label = field(sample('read_before_the_label_edits').body, 'farm', 'label');
    expect(checkEntry(label, '', true)).toEqual({ kind: 'valid', remove: true, value: undefined });
    for (const raw of ['', '   ']) expect(checkEntry(label, raw).kind).toBe('invalid');
  });
  it('RL12 every coin entry offers a tab; switching back deletes even stored null', () => {
    const tab = field(sample('read_before_the_kind_switches').body, 'farm', 'dissonant_tab');
    expect(checkEntry(tab, 'attack')).toEqual({ kind: 'valid', remove: false, value: 'attack' });
    const nullTab = field(
      sample('read_after_the_tab_was_set_to_null').body,
      'farm',
      'dissonant_tab',
    );
    expect(checkEntry(nullTab, '')).toEqual({ kind: 'valid', remove: true, value: undefined });
    expect(
      fields(sample('read_before_the_kind_switches').body, 'farm')
        .map((f) => f.key)
        .join('|'),
    ).not.toContain('kind');
  });
  it.each(['rides', 'cycles'])('RL13 %s leaves typed values and ranges to the server', (key) => {
    const f = field(
      sample('read_before_the_limit_edits').body,
      key === 'rides' ? 'quest_sm' : 'quest_ilm',
      key,
    );
    for (const raw of ['0', '-1', '2.5', '5', '"5"']) {
      const expected: unknown = JSON.parse(raw);
      expect(checkEntry(f, raw)).toEqual({ kind: 'valid', remove: false, value: expected });
    }
    expect(f.removable).toBe(key === 'rides');
    expect(
      fields(sample('read_before_the_limit_edits').body, 'farm').some((f) => f.key === key),
    ).toBe(false);
  });
  it('RL11 policy routes preserve family and exact encoded policy identity', () => {
    const target = { kind: 'ability', family: 'uw_policies', policy: 'a / b' } as const;
    expect(hrefFor('abilities', target)).toBe('#abilities/uw_policies/a%20%2F%20b');
    expect(matchHash('#abilities/uw_policies/a%20%2F%20b')).toEqual({
      kind: 'page',
      id: 'abilities',
      target,
    });
    expect(matchHash('#abilities/other/x').kind).toBe('not_found');
  });
});
