import { entry, made } from './entry.ts';
import { settings } from './settings.ts';
export const abilities = {
  contextChanged: settings.outcomes.context_changed,
  pageName: entry('Abilities'),
  intro: entry('Saving changes future plans; it does not fire abilities or change a live battle.'),
  target: entry('Emulator / account / profile'),
  currentTarget: entry('Current target'),
  state: entry('State'),
  unknown: entry('Not reported'),
  noTarget: entry('Select an emulator and profile to read saved abilities.'),
  read: entry('Read saved result'),
  reading: entry('Reading policies, settings and vocabulary…'),
  readError: entry(
    'The target or its saved data could not be read. Drafts are kept; saving is blocked.',
  ),
  stale: entry('Read saved result to check the target and both revisions before saving.'),
  scope: entry(
    'Plans apply to the editing profile; the global override applies to the active profile.',
    'Each save writes one policy or the override once. Saves are refused if the account, active profile or saved file changed.',
  ),
  global: entry('Global Demon Mode timing'),
  activeOnly: entry('Return editing to the active profile to change these'),
  globalUnavailable: entry(
    'The active profile’s settings could not be read. Read saved result before changing the global override.',
  ),
  saveTarget: entry('Save target:'),
  space: made(' '),
  checking: entry('Checking the runtime context and editing profile before saving…'),
  targetCheck: {
    context_changed: entry(
      'The runtime context or editing target changed. Nothing was sent; the draft is kept.',
    ),
    context_unconfirmed: entry(
      'The runtime context could not be confirmed. Nothing was sent; the draft is kept.',
    ),
    target_missing: entry(
      'The editing profile is no longer listed or editable. Nothing was sent; the draft is kept.',
    ),
    target_unconfirmed: entry(
      'The editing profile list could not be read. Nothing was sent; the draft is kept.',
    ),
  },
  override: entry('Use Demon Mode whenever ready'),
  overrideHelp: entry(
    'Overrides held Demon Mode rescue timing; this setting does not verify ownership, recognition, readiness or firing.',
    'Applies to every run in the active profile.',
  ),
  readOnly: entry(
    'This profile or settings module is read-only. Copy the starter or promote a draft in Profiles before editing.',
  ),
  plans: entry('Saved plans'),
  morePlans: entry('Show more plans'),
  uw: entry('Ultimate Weapons'),
  rescue: entry('Rescue'),
  empty: entry('No saved plans in this family. Enter a new plan ID to create one.'),
  id: entry('New plan ID'),
  planId: entry('ID'),
  discardId: entry('Discard new plan ID'),
  explicitBounds: entry('Use explicit wave bounds'),
  optionalBounds: entry('Unset uses the backend default.', 'No threshold is calculated here.'),
  bounds: entry('Wave bounds'),
  idHelp: entry('Use letters, numbers, underscores or hyphens; choose an unused ID.'),
  create: entry('Create draft'),
  copy: entry('Copy selected to draft'),
  label: entry('Plan label'),
  dirty: entry('Unsaved draft'),
  clean: entry('Saved configuration'),
  consumers: entry('Used by saved runs'),
  unused: entry('No saved run references this plan.'),
  baseline: entry('Baseline weapons'),
  unspecified: entry('Leave unspecified'),
  on: entry('On'),
  off: entry('Off'),
  skipped: entry('Ownership not recorded — skipped here.'),
  owned: entry(
    'Unrecorded weapons stay in the reusable plan.',
    'Recorded ownership is separate from screen recognition and readiness.',
  ),
  cl: entry('Chain Lightning'),
  clMode: entry('Chain Lightning mode'),
  absent: entry('No Chain Lightning choreography'),
  ranges: entry(
    'Whole waves; minimum and maximum included.',
    'The backend chooses randomized values. Fleet marks and next due times are not published here.',
  ),
  arm: entry('Arming'),
  armMode: entry('Arm scope'),
  always: entry('Always armed'),
  secondWind: entry('After Second Wind'),
  duration: entry(
    'Blank watch: rest of run. Blank immunity: backend observation. Durations must be positive. Always armed removes Second Wind duration fields.',
  ),
  endSprint: entry('End the intro sprint after the Second Wind window'),
  sprintHelp: entry('Ending the intro sprint unlocks the ability row. It does not end the battle.'),
  rules: entry('Ordered rescue rules'),
  add: entry('Add rule'),
  when: entry('Condition'),
  action: entry('Action'),
  unsupported: entry(
    'This rule is read-only: its trigger or action is unsupported here. It is preserved exactly; explicit Delete rule removes only this rule. Surrender retry requires separate own-battle authority.',
  ),
  unavailable: entry(
    'New surrender_retry, switch_cards, death_screen and stop_after_run rules are unavailable. Existing unsupported rules remain inspectable.',
  ),
  repeat: entry('Repeat this rule'),
  cooldown: entry('Rule cooldown floor (seconds)'),
  cooldownHelp: entry(
    'The backend validates repeats and cooldowns; fast wall-watch rules may refuse them.',
    'Repeat and refire_sec are main-loop fields. Action throttle_sec and refire_guard_sec are validated by the backend. No cooldown countdown or next fire time is calculated here.',
  ),
  up: entry('Move up'),
  down: entry('Move down'),
  remove: entry('Delete rule'),
  save: entry('Save plan'),
  saveGlobal: entry('Save global override'),
  cancel: entry('Cancel edits'),
  delete: entry('Delete unused plan'),
  confirmDelete: entry('Delete this unused plan?'),
  deleteHelp: entry(
    'This explicitly deletes this plan from the named profile. The backend also refuses deletion if a saved run references it.',
  ),
  keep: entry('Keep editing'),
  cancelTitle: entry('Discard this draft?'),
  cancelHelp: entry('Discard only this plan’s unsaved edits and restore its latest saved value.'),
  compare: entry('Review saved and proposed values'),
  original: entry('Draft starting value'),
  latest: entry('Latest saved value'),
  proposed: entry('Proposed value'),
  review: entry('I reviewed the mode or rule change and its removed fields'),
  reviewNeeded: entry('Review the saved and proposed values before saving this structural change.'),
  reconcile: entry('I reviewed the saved values; use these revisions and keep my draft'),
  blocked: entry(
    'Save is unavailable. Read saved result and check the target, revision, field errors, read-only scope and any unresolved request.',
  ),
  sending: entry('Saving once… Newer typing stays in this draft.'),
  notSent: entry(
    'No save was sent. The draft is kept. Read the saved result before another explicit Save.',
  ),
  saved: entry('Saved in the profile. No game action was performed.'),
  deletedDraft: entry(
    'The saved plan was deleted. Newer typing remains here as a draft. A new explicit Save is required to create it again.',
  ),
  unknownResult: entry(
    'Save outcome unknown. Read saved result for this original target. This request will never be resent automatically.',
  ),
  refused: entry(
    'The backend refused this save. The exact draft is kept. Read saved result before another explicit Save.',
  ),
  conflict: entry(
    'The saved revision changed or the target is read-only. Read saved result, compare, then explicitly use the new revisions if appropriate.',
  ),
  readApplied: entry('A later read holds the submitted value. Newer typing is kept.'),
  readUnchanged: entry(
    'A later read still holds the original value and revision. The draft is kept; another Save requires a fresh click.',
  ),
  problems: entry('Backend validation problems'),
  warnings: entry('Backend warnings'),
  validator: entry('Validator status'),
  evidence: entry('Recorded ownership and readiness'),
  readiness: entry('Readiness'),
  recordedVerification: entry('Recorded ability verification'),
  yes: entry('Yes'),
  no: entry('No'),
  readinessUnchecked: entry('Not checked by this editor'),
  recognition: entry('Open Recognition'),
  evidenceHelp: entry(
    'Policy choices do not verify abilities or buttons. Unverified abilities and no confirmed wall are advisories; ownership failures when bound to runs are backend validation problems.',
  ),
  numericError: entry(
    'Enter a finite number. The raw text is kept; blank optional fields are omitted.',
  ),
  required: entry('Required'),
  minimum: entry('Minimum'),
  maximum: entry('Maximum'),
  unknownFields: entry('Additional saved fields (preserved)'),
  savedDetails: entry('Saved policy data'),
};
const labels: Readonly<Record<string, string>> = {
  always_on: 'Always on',
  fleet_marks: 'Around fleet marks',
  off_until_wave: 'Off until wave range',
  off: 'Off',
  always_on_above: 'Always on above wave',
  on_above: 'Turn on above wave',
  pre_mark_waves: 'Waves before fleet mark',
  off_after_waves: 'Waves after fleet mark',
  watch_sec: 'Watch duration (seconds; blank = unspecified)',
  immunity_sec: 'Immunity duration (seconds; blank = unspecified)',
};
export const abilityLabel = (key: string) => made(labels[key] ?? key.replaceAll('_', ' '));
export const ruleNumber = (index: number) => made(`Rule ${index + 1}`);
export const ruleActionLabel = (action: 'up' | 'down' | 'delete', index: number) =>
  made(
    `${action === 'up' ? 'Move up' : action === 'down' ? 'Move down' : 'Delete rule'} ${index + 1}`,
  );
export const ruleMoved = (index: number) =>
  made(`Rule order changed. Focus is at position ${index + 1}.`);
