// @ts-check
import { fencedCase, fencedSourceBefore } from '../profilePatchFenced.ts';
import { dashboardPagesCase } from '../dashboardFixture.ts';
import { entries, own } from '../../../src/core/read.ts';

export const FENCED_WRITES = [
  'active_plan_saved',
  'active_plan_deleted',
  'active_gather_label_saved',
  'active_gather_copy_saved',
  'active_gather_template_created',
  'active_unused_gather_deleted',
  'active_chores_saved',
  'active_uw_policy_saved',
  'active_uw_policy_deleted',
  'active_rescue_policy_saved',
  'active_rescue_policy_deleted',
  'active_run_entry_label_saved',
  'active_run_entry_field_deleted',
];

/** @type {Readonly<Record<string, import('./scenarios.mjs').Scenario>>} */
export const PROFILE_PATCH_FENCED_SCENARIOS = Object.fromEntries(
  FENCED_WRITES.flatMap((name) =>
    [false, true].map((refused) => [
      `profile_patch_fenced_${name}${refused ? '_refused' : ''}`,
      {
        description: `F6-14b C-02 ${name}: exact fenced ${refused ? 'refusal' : 'save/readback'}`,
        harvest: 'off',
        get: {
          config: 'profile_target.inactive_editor_context_before_save',
          profileFiles: 'profile_target.same_account_inactive_target_listed',
          profileSource: `profile_patch_fenced_before:${name}`,
          settings: 'profile_patch_fenced_settings',
          schedule: 'schedule_status.scheduler_not_observed',
        },
        writes: {
          profilePatch: {
            // The context guard precedes path validation. Replay the genuine plan 409
            // unchanged for every writer; only the submitting path differs.
            case: `profile_patch_fenced.${refused ? 'active_patch_refused_after_account_switch' : name}`,
            reads: {
              // Refusal cannot mutate the source. Only the plan submission has its own
              // refusal GET; other paths keep their recorded pre-write source unchanged.
              profileSource: refused
                ? name === 'active_plan_saved'
                  ? 'profile_patch_fenced.read_after_active_patch_refused_after_account_switch'
                  : `profile_patch_fenced_before:${name}`
                : `profile_patch_fenced.read_after_${name}`,
            },
          },
        },
      },
    ]),
  ),
);

/** @param {string | undefined} reference */
export function profilePatchFencedRead(reference) {
  if (reference === 'profile_patch_fenced_settings') {
    const found = dashboardPagesCase('settings.populated_account_every_module');
    // Modified recorded settings metadata: match the recorded alpha runtime. All module
    // values remain recorded; the source/revision always comes from the fenced family.
    const body = /** @type {Record<string, unknown>} */ (found.body);
    return {
      status: found.status,
      body: {
        ...body,
        context: { ...Object.fromEntries(entries(body['context'])), account: 'alpha' },
      },
    };
  }
  if (!reference?.startsWith('profile_patch_fenced_before:')) return undefined;
  const name = reference.split(':')[1] ?? '';
  const found = fencedSourceBefore(name);
  if (name !== 'active_plan_saved')
    return { status: found.status, body: Object.fromEntries(entries(found.body)) };
  const body = /** @type {Record<string, unknown>} */ (found.body);
  const profile = /** @type {Record<string, unknown>} */ (body['profile']);
  const request = /** @type {Record<string, unknown>} */ (fencedCase(name).request.body);
  // Modified recorded source: the UI cannot rename/create an arbitrary spare_day.
  // Start from the recorded proposed plan with count 1; clearing that optional field
  // through the UI produces the exact recorded whole-plan request at its original CAS.
  return {
    status: found.status,
    body: {
      ...body,
      profile: {
        ...profile,
        plan: {
          ...Object.fromEntries(entries(own(request, 'value'))),
          days: { spare_day: [{ block: 'coin', blueprint: 'farm', count: 1 }] },
        },
      },
    },
  };
}
