import { entry, made } from './entry.ts';
import { editingTarget } from './editingTarget.ts';

/**
 * The Collection page (backend/logs/reviews/ui-pages/collection-brief.md, C1 to C6): what
 * saved gathering policies, global rewards and between-run chores may do. Saved choices,
 * never observed collection: nothing here claims a reward, a quota or a chore result.
 */
export const collection = {
  pageName: entry('Collection'),
  savedSettings: entry('Saved settings'),
  activeProfile: entry('Active profile:'),
  noActiveProfile: entry('none'),
  activeOnly: entry('Return editing to the active profile to change these'),
  scope: entry(
    'Saved collection settings of the active profile',
    'They apply when a run starts or at its next driver or battle boundary; nothing here starts, restarts or taps anything. Harvesting on and off stays on the Dashboard.',
  ),
  profile: entry('Profile:'),
  dashboard: entry('Harvesting on or off: Dashboard'),
  readAge: (seconds: number) =>
    made(
      seconds < 60
        ? `read ${String(seconds)} s ago`
        : `read ${String(Math.floor(seconds / 60))} min ago`,
    ),
  reading: entry('Reading the saved settings'),
  unavailable: entry("The saved settings can't be read"),
  otherContext: entry(
    'The last settings read was for another emulator, account or profile',
    'Its values are not shown, used or counted as a save of yours. Read the saved settings again; your unsaved edits here are kept.',
  ),
  check: entry('Read the saved settings again'),
  rewards: {
    title: entry('Global rewards'),
    note: entry(
      'A saved choice is not a collected reward',
      'These choices let automation try the reward when it is due; a choice being on does not mean the reward was claimed.',
    ),
    quotaLabel: entry('Daily quota and reset time'),
    quota: entry(
      'Not reported',
      'The server does not report how many rewards are left today or when they reset, so this page does not guess.',
    ),
  },
  policies: {
    title: entry('Gathering policies'),
    list: entry('Saved policies'),
    new: entry('New policies'),
    more: entry('More policies'),
    intro: entry(
      'Changing a policy changes every run that uses it',
      'Each run uses one policy; several runs can share it. Choose which policy a run uses in its saved settings on the Runs page. New policies remain unused until chosen there.',
    ),
    none: entry('This profile has no gathering policies'),
    noTemplate: entry(
      'No template gathering defaults can be read',
      'Creation needs the starting values published by the server. Read the saved settings again; this page never invents defaults.',
    ),
    usedBy: entry('Used by'),
    unused: entry('Not used by any run'),
    open: entry('Edit'),
    missing: entry('A run names a policy this profile does not define:'),
    problems: entry('Profile check found problems'),
    validatorError: entry("The profile check couldn't run"),
  },
  editor: {
    deletion: entry('Policy deletion:'),
    label: entry('Name shown'),
    saveLabel: entry('Save name'),
    gather: entry('Rewards gathered during runs'),
    ad: entry('Ad gems'),
    orbit: entry('Orbiting gems'),
    timing: entry(
      'Timers run start to start',
      'The interval between collection windows runs from one window start to the next, collected or missed. Retries and backoff stay inside one window. A missed window is not made up later and does not promise a collection.',
    ),
    geometry: entry(
      'Tap geometry (native screen pixels)',
      "The orbit center, radius and spread are in the emulator screen's native pixels, not this page's. Editing them sends no tap.",
    ),
    guildStore: entry(
      'Spending: lets automation spend guild currency in the guild Store',
      'Only after you save it on; nothing is bought from this page.',
    ),
    runsLink: entry('Saved settings in Runs'),
    remove: entry('Delete this policy'),
    confirmRemove: entry(
      'Delete this policy? No run can use it afterwards; the server keeps a profile backup.',
    ),
    confirm: entry('Delete'),
    cancel: entry('Cancel'),
  },
  copy: {
    title: entry('Copy a policy'),
    intro: entry(
      'All saved source fields are copied',
      'This includes fields this page does not show.',
    ),
    from: entry('Copy from'),
    id: entry('New identifier'),
    idHelp: entry('Lowercase letters, digits and _, starting with a letter'),
    label: entry('Name shown (optional)'),
    save: entry('Save copy'),
  },
  create: {
    title: entry('Create a policy from a template'),
    intro: entry('Template values are saved unchanged', 'Creating a policy starts nothing.'),
    template: entry('Template gathering default'),
    id: entry('New policy identifier'),
    save: entry('Save policy'),
  },
  chores: {
    title: entry('Between-run chores'),
    saved: entry('Saved chores'),
    intro: entry(
      'On lets a chore run between runs when it is due',
      'Due means its local day or interval allows it; a chore being on is not a record that it ran or succeeded.',
    ),
    names: {
      quest_scan: entry('Quest scan', 'Reads the quest list between runs.'),
      shatter: entry('Shatter surplus blue modules'),
      housekeeping: entry('Housekeeping', 'Small tidy-up steps between runs.'),
    },
    unknown: entry('Not known to this version: kept as saved'),
    destroys: entry('Destroys modules when it runs; cannot be undone'),
    on: entry('On'),
    save: entry('Save chores'),
  },
  reasons: {
    ...editingTarget.blocked,
    context_unknown: entry('The emulator and account are not confirmed yet'),
    source_not_read: entry('The profile has not been read yet'),
    read_only: entry('The active profile cannot be edited here'),
    save_pending: entry('Waiting until the last save is read back'),
    enter_label: entry('Enter a name'),
    no_changes: entry('No change to save'),
    policy_in_use: entry('Used by a run; give those runs another policy in Runs first'),
    choose_source: entry('Choose a policy to copy'),
    no_policy_to_copy: entry('No saved policy to copy; create one from a template'),
    template_not_read: entry('Choose a readable template gathering default'),
    enter_id: entry('Enter an identifier'),
    bad_id: entry('Use lowercase letters, digits and _, starting with a letter'),
    id_taken: entry('A policy with this identifier exists'),
    vocab_not_read: entry('The chore list has not been read yet'),
  },
  notSent: {
    target_changed: entry('Not sent - the editing target changed; your draft is kept'),
    target_missing: entry(
      'Not sent - the editing target is no longer available; your draft is kept',
    ),
    target_unconfirmed: entry(
      'Not sent - the editing target could not be confirmed; your draft is kept',
    ),
    context_unconfirmed: entry(
      "Not sent - the current emulator, account and profile couldn't be confirmed",
      'The page reads the configuration before each profile change; that read did not name a context. Nothing was sent.',
    ),
    context_changed: entry(
      'Not sent - the account or profile changed; check the page and save again',
    ),
    collection_write_in_flight: entry('Not sent - the last save is still waiting for its reply'),
  },
} as const;
