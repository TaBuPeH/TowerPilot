import { describe, expect, it } from 'vitest';
import {
  actionControlsBlocked,
  actionsView,
  liveConstraints,
  type ActionsReading,
} from '../../../src/core/actions.ts';
import { copy } from '../../../src/copy/catalogue.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { liveControls, liveFieldNullable, reviewDraft } from '../../../src/core/runControls.ts';
import { dashboardPagesCase, dashboardPagesCaseNames } from '../../support/dashboardFixture.ts';

/**
 * A2-7 AC2 (brief backend/logs/reviews/ui-pages/a2-7-actions-brief.md): the live editor's fields
 * come from each item's descriptor (core/runControls liveControls) over the landed r17b
 * projection cases. Test-local changes to a recorded body are named where they are made.
 */
const VALUES = 'battle_actions_projection.values_carry_every_field_in_force';
const OVERRIDE = 'battle_actions_projection.a_live_control_changes_the_value_in_force';

function battleOf(path: string): Record<string, unknown> {
  return ownPath(dashboardPagesCase(path).body, 'battle') as Record<string, unknown>;
}

function controlsOf(battle: unknown) {
  const view = actionsView(battle);
  return liveControls(
    ownPath(battle, 'capabilities', 'controls'),
    own(battle, 'effective'),
    liveConstraints(view, undefined),
    view,
  );
}

/** The descriptor fields of the item keyed `key` (test-local edits go through this). */
function descriptorFields(battle: unknown, key: string): Record<string, unknown>[] {
  const items = ownPath(battle, 'runtime', 'actions', 'items') as Record<string, unknown>[];
  const item = items.find((found) => found['key'] === key);
  return ownPath(item, 'descriptor', 'fields') as Record<string, unknown>[];
}

describe('AC2 live fields from the action descriptors (core/runControls liveControls)', () => {
  it('AC2 every recorded projection gives each controlled field its descriptor type, bounds, unit and label', () => {
    const projected = dashboardPagesCaseNames('battle_actions_projection').filter(
      (name) => name !== 'projection_never_built_the_actions_key_is_absent',
    );
    expect(projected).toHaveLength(13);
    for (const name of projected) {
      const battle = battleOf(`battle_actions_projection.${name}`);
      const controls = new Set(ownPath(battle, 'capabilities', 'controls') as string[]);
      const live = controlsOf(battle);
      const items = ownPath(battle, 'runtime', 'actions', 'items') as Record<string, unknown>[];
      for (const item of items) {
        const key = String(item['key']);
        const group = live.groups.find((found) => found.routine === key);
        const expected = descriptorFields(battle, key).filter(
          (field) =>
            field['live'] === true && controls.has(`routines.${key}.${String(field['path'])}`),
        );
        expect(
          group?.fields.map((field) => field.field),
          `${name} ${key}`,
        ).toEqual(expected.map((field) => field['path']));
        for (const field of expected) {
          const path = `routines.${key}.${String(field['path'])}`;
          const shown = group?.fields.find((found) => found.path === path);
          expect(shown?.kind, path).toBe(field['type'] === 'bool' ? 'toggle' : 'number');
          if (field['type'] === 'number') {
            expect(shown?.min, path).toBe(field['min']);
            expect(shown?.max, path).toBe(field['max']);
          }
          expect(live.descriptions.get(path), path).toEqual({
            label: field['label'],
            unit:
              field['unit'] === 's'
                ? { kind: 'seconds' }
                : field['unit'] === null
                  ? { kind: 'none' }
                  : { kind: 'other', text: field['unit'] },
            nullable: field['nullable'],
          });
        }
        expect(live.itemLabels.get(key)).toBe(item['label']);
      }
      expect(live.unrecognised).toEqual([]);
      expect(live.unaddressable).toEqual([]);
    }
  });

  it('AC2 the floating gems descriptor labels and its 0.5 to 30 tap bound come through as sent', () => {
    const live = controlsOf(battleOf(VALUES));
    expect(
      ['enabled', 'interval_sec', 'window_sec', 'tap_interval_sec'].map(
        (field) => live.descriptions.get(`routines.floating_gems.${field}`)?.label,
      ),
    ).toEqual([
      'Enabled',
      'Retry spacing after unconfirmed taps',
      'Maximum floating gem claim attempt',
      'Time between tap sets while seeking',
    ]);
    const tap = live.groups
      .find((group) => group.routine === 'floating_gems')
      ?.fields.find((field) => field.field === 'tap_interval_sec');
    expect(tap).toMatchObject({ kind: 'number', min: 0.5, max: 30, value: 5 });
  });

  it('AC2 the gates the projection does not describe (S4) keep their switches, and a battle without a projection keeps every field as read', () => {
    const live = controlsOf(battleOf(VALUES));
    expect(
      live.groups
        .filter((group) => ['rewards', 'abilities', 'rescue', 'repeat'].includes(group.routine))
        .map((group) => [group.routine, group.fields.map((field) => field.kind)]),
    ).toEqual([
      ['rewards', ['toggle']],
      ['abilities', ['toggle']],
      ['rescue', ['toggle']],
      ['repeat', ['toggle']],
    ]);
    const compat = controlsOf(
      battleOf('battle_actions_projection.projection_never_built_the_actions_key_is_absent'),
    );
    expect(compat.descriptions.size).toBe(0);
    expect(compat.groups.flatMap((group) => group.fields).length).toBe(14 - 1);
  });

  it('AC2 test-local: a field type the page does not know is shown apart and never editable', () => {
    const battle = battleOf(VALUES);
    const window = descriptorFields(battle, 'ad_gems').find(
      (field) => field['path'] === 'window_sec',
    );
    if (window === undefined) throw new Error('no window field');
    window['type'] = 'duration';
    const live = controlsOf(battle);
    expect(live.unrecognised).toEqual([
      {
        path: 'routines.ad_gems.window_sec',
        routine: 'ad_gems',
        field: 'window_sec',
        type: 'duration',
        label: 'Window',
      },
    ]);
    const fields = live.groups.flatMap((group) => group.fields.map((field) => field.path));
    expect(fields).not.toContain('routines.ad_gems.window_sec');
    expect(reviewDraft(live.groups, { 'routines.ad_gems.window_sec': '30' })).toEqual({
      changes: {},
      invalid: { 'routines.ad_gems.window_sec': 'not_editable' },
    });
  });

  it('AC2 test-local: a listed control the descriptor does not mark live is not a live field', () => {
    const battle = battleOf(VALUES);
    const interval = descriptorFields(battle, 'shopping').find(
      (field) => field['path'] === 'interval_sec',
    );
    if (interval === undefined) throw new Error('no interval field');
    interval['live'] = false;
    const live = controlsOf(battle);
    expect(
      live.groups.find((group) => group.routine === 'shopping')?.fields.map((f) => f.field),
    ).toEqual(['enabled']);
    expect(reviewDraft(live.groups, { 'routines.shopping.interval_sec': '120' }).invalid).toEqual({
      'routines.shopping.interval_sec': 'not_editable',
    });
  });

  it('AC2 test-local: a descriptor field the battle does not list in capabilities.controls is never offered', () => {
    const battle = battleOf(VALUES);
    const controls = ownPath(battle, 'capabilities') as Record<string, unknown>;
    controls['controls'] = (controls['controls'] as string[]).filter(
      (path) => path !== 'routines.ad_gems.window_sec',
    );
    const live = controlsOf(battle);
    expect(
      live.groups.find((group) => group.routine === 'ad_gems')?.fields.map((f) => f.field),
    ).toEqual(['enabled', 'interval_sec']);
  });

  it('AC2 test-local: units other than seconds and no unit are kept as sent', () => {
    const battle = battleOf(VALUES);
    const fields = descriptorFields(battle, 'floating_gems');
    const tap = fields.find((field) => field['path'] === 'tap_interval_sec');
    const window = fields.find((field) => field['path'] === 'window_sec');
    if (tap === undefined || window === undefined) throw new Error('missing fields');
    tap['unit'] = 'taps';
    window['unit'] = null;
    const live = controlsOf(battle);
    expect(live.descriptions.get('routines.floating_gems.tap_interval_sec')?.unit).toEqual({
      kind: 'other',
      text: 'taps',
    });
    expect(live.descriptions.get('routines.floating_gems.window_sec')?.unit).toEqual({
      kind: 'none',
    });
  });

  it('AC2 test-local: an item with instance other than its id and no published key is listed as not addressable', () => {
    const battle = battleOf(VALUES);
    const items = ownPath(battle, 'runtime', 'actions', 'items') as Record<string, unknown>[];
    const copy = structuredClone(items[0]) as Record<string, unknown>;
    delete copy['key'];
    copy['instance'] = 'ad_gems_two';
    items.push(copy);
    const live = controlsOf(battle);
    expect(live.unaddressable).toEqual([
      { id: 'ad_gems', instance: 'ad_gems_two', label: 'Ad gems' },
    ]);
    expect(live.groups.find((group) => group.routine === 'ad_gems')?.fields).toHaveLength(3);
  });

  it('AC2 test-local: an action whose only item names no key offers none of its listed controls', () => {
    const battle = battleOf(VALUES);
    const items = ownPath(battle, 'runtime', 'actions', 'items') as Record<string, unknown>[];
    const ad = items.find((item) => item['key'] === 'ad_gems');
    if (ad === undefined) throw new Error('no ad gems item');
    delete ad['key'];
    ad['instance'] = 'ad_gems_one';
    const live = controlsOf(battle);
    expect(live.unaddressable).toEqual([
      { id: 'ad_gems', instance: 'ad_gems_one', label: 'Ad gems' },
    ]);
    expect(live.groups.find((group) => group.routine === 'ad_gems')).toBeUndefined();
    expect(reviewDraft(live.groups, { 'routines.ad_gems.enabled': false }).invalid).toEqual({
      'routines.ad_gems.enabled': 'not_editable',
    });
    expect(live.groups.find((group) => group.routine === 'floating_gems')?.fields).toHaveLength(4);
  });

  it('AC2 every code that can close a live change has its visible reason in the Runs copy', () => {
    // Every reading kind and reason of core/actions ActionsReading (the gate reason is a code
    // string; RunEditor would otherwise fall back to "No changes to save").
    const readings: ActionsReading[] = [
      { kind: 'current' },
      { kind: 'absent' },
      { kind: 'old', ageSec: 2 },
      { kind: 'unknown', reason: 'action_report_time_invalid' },
      { kind: 'unknown', reason: 'battle_report_old' },
      { kind: 'unknown', reason: 'battle_time_invalid' },
      { kind: 'read_only', reason: 'action_schema_newer' },
      { kind: 'read_only', reason: 'action_context_mismatch' },
    ];
    const codes = readings.flatMap((reading) => actionControlsBlocked(reading) ?? []);
    expect(codes).toHaveLength(6);
    const missing = [...codes, 'not_nullable'].filter(
      (code) => !Object.hasOwn(copy.runs.reasons, code),
    );
    expect(missing).toEqual([]);
  });

  it('AC2 a field is reset to its starting value only where its descriptor is nullable', () => {
    const battle = battleOf(OVERRIDE);
    const view = actionsView(battle);
    expect(liveFieldNullable(view, 'ad_gems', 'interval_sec')).toBe(true);
    expect(liveFieldNullable(view, 'rewards', 'enabled')).toBe(true);
    expect(liveFieldNullable(null, 'ad_gems', 'interval_sec')).toBe(true);
    const interval = descriptorFields(battle, 'ad_gems').find(
      (field) => field['path'] === 'interval_sec',
    );
    if (interval === undefined) throw new Error('no interval field');
    interval['nullable'] = false;
    expect(liveFieldNullable(actionsView(battle), 'ad_gems', 'interval_sec')).toBe(false);
  });
});
