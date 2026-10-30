import { entry, made } from './entry.ts';
import { serverDateText } from './format.ts';
import { ageAgo } from './dashboard/shell.ts';

function plural(n: number, one: string, many: string): string {
  return `${String(n)} ${n === 1 ? one : many}`;
}

/**
 * Catalogue entries of the Profiles page (backend/logs/reviews/ui-pages/profiles-brief.md,
 * P1 to P5). "Local game account" and "Automation profile" are kept distinct; neither is
 * a game login or a detected identity.
 */
export const profiles = {
  pageName: entry('Profiles'),
  account: {
    title: entry('Local game account'),
    binding: entry('Account binding'),
    scope: entry(
      'A name you choose on this computer for one game account',
      'It is not your game login and is never detected from the game. Choosing it binds this emulator to that account and its automation profile; it does not end a run, reconnect or scan.',
    ),
    emulator: entry('Emulator:'),
    inUse: entry('In use on this emulator:'),
    noneActive: entry('No local account is chosen for this emulator'),
    noAccounts: entry('No local accounts yet - create one below'),
    picker: entry('Local game account'),
    use: entry('Use this account'),
    reading: entry('Reading the accounts'),
    settling: entry('Reading the accounts again after your request'),
    unavailable: entry("The accounts can't be read"),
    readAge: (seconds: number) => made(`read ${ageAgo(seconds)}`),
    keeps: (edits: number, unresolved: number) =>
      made(
        `${plural(edits, 'unsaved edit', 'unsaved edits')} and ${plural(unresolved, 'request', 'requests')} without an answer stay with the current account and profile. They come back when you switch back; nothing is moved to the other account.`,
      ),
    confirmSwitch: entry('Switch and keep them there'),
    cancel: entry('Cancel'),
  },
  create: {
    title: entry('Create a local account'),
    id: entry('Identifier'),
    idHelp: entry('Letters, digits, underscores or hyphens, starting with a letter'),
    label: entry('Name shown (optional)'),
    importCurrent: entry("Copy this installation's current settings and calibration into it"),
    importCondition: entry(
      'Only for this same game account.',
      'Another account would get screens and settings learned on this one.',
    ),
    submit: entry('Create account'),
  },
  tier: {
    title: entry('Highest unlocked tier'),
    input: entry('Highest unlocked tier'),
    range: entry('1 to 20'),
    current: entry('Saved:'),
    notSaved: entry('Saved tier unavailable'),
    default: entry('Default:'),
    defaultNote: entry('not recorded in the profile'),
    recordedState: entry('Profile tier'),
    value: (tier: number) => made(String(tier)),
    byOperator: entry('entered by you, not read from the game'),
    byOther: entry('recorded by'),
    source: entry('Source'),
    sourceUnknown: entry('source not reported'),
    save: entry('Save highest tier'),
    invalid: {
      empty: entry('Enter a tier'),
      not_whole: entry('Use a whole number, like 12'),
      out_of_range: entry('Use a tier from 1 to 20'),
    },
  },
  setup: {
    title: entry('Setup'),
    connection: entry('Connection and setup'),
    toolsLabel: entry('Connection tools'),
    progress: entry('Setup progress'),
    readiness: entry('Run readiness'),
    tools: {
      installed: entry('Installed'),
      not_installed: entry('Not installed'),
      unknown: entry('Not read'),
    },
    calibration: {
      running: entry('A setup step is running'),
      restore_pending: entry('Original modules are waiting to be restored'),
      idle: entry('No setup step running'),
      unknown: entry('Setup progress: not read'),
    },
    ready: entry('Runs ready to start'),
    notReady: entry('Not ready'),
    unknown: entry('Not checked'),
    runsUnknown: entry('Run readiness: not read'),
    note: entry(
      'A saved account does not mean its screens are learned',
      'Recognition shows what each run still needs; nothing on this page starts a setup step or a scan.',
    ),
    emulators: entry('Emulators'),
    recognition: entry('Recognition'),
  },
  files: {
    title: entry('Automation profile'),
    inventory: entry('Profile files'),
    scope: entry(
      'The saved settings automation uses for this account',
      'Copying a profile or saving a scan draft only writes a file. A profile with problems cannot be activated.',
    ),
    valid: entry('No problems found'),
    problems: (n: number) => made(plural(n, 'problem', 'problems')),
    warnings: (n: number) => made(plural(n, 'warning', 'warnings')),
    validatorUnavailable: entry("The profile check couldn't run - its validity is unknown"),
    sourceUnavailable: entry("The active profile can't be read"),
    reading: entry('Reading the profiles'),
    settling: entry('Reading the profiles again after your request'),
    unavailable: entry("The profiles can't be read"),
    none: entry('No profile files yet'),
    columns: {
      name: entry('Name'),
      kind: entry('Kind'),
      modified: entry('Modified'),
    },
    kinds: {
      starter: entry('Shipped starter'),
      draft: entry('Scan draft'),
      own: entry('Your profile'),
    },
    activeMark: entry('Active'),
    modified: (seconds: number) => made(serverDateText(seconds)),
    modifiedUnknown: entry('not reported'),
  },
  activate: {
    title: entry('Activate a profile'),
    effect: entry('Automation uses this profile from its next start.'),
    picker: entry('Profile to activate'),
    choose: entry('Choose a profile'),
    submit: entry('Activate'),
    checking: entry('Checking the profile'),
    ready: entry('Checked: no problems found'),
  },
  /** F6-14 PE1: use no profile (classic "none - legacy constants"). */
  unbind: {
    account: entry('Account:'),
    installation: entry('this installation'),
    emulator: entry('Emulator:'),
    profile: entry('Current profile:'),
    title: entry('Use no profile'),
    active: entry('Active profile:'),
    none: entry('none - the scheduler runs its legacy rules'),
    submit: entry('Use no profile'),
    consequence: entry(
      'Unbinds the active profile; automation uses legacy rules from its next start. Nothing starts or stops now.',
      'The profile file stays, and Activate binds it again.',
    ),
    confirm: entry('Use no profile?'),
    effect: entry(
      'The active profile is unbound; the scheduler runs its legacy rules from its next start. The profile file is kept. Nothing starts or stops now.',
    ),
    send: entry('Use no profile'),
    cancel: entry('Cancel'),
  },
  copy: {
    title: entry('Copy a profile'),
    from: entry('Copy from'),
    name: entry('New profile name'),
    overwrite: entry('Replace the existing profile with this name'),
    submit: entry('Copy'),
  },
  promote: {
    title: entry('Save a scan draft as a profile'),
    draft: entry('Scan draft'),
    noDrafts: entry('No scan drafts'),
    name: entry('Profile name'),
    base: entry('Start from'),
    overwrite: entry('Replace the existing profile with this name'),
    submit: entry('Save as profile'),
  },
  reasons: {
    accounts_not_read: entry('The accounts have not been read yet'),
    choose_account: entry('Choose an account'),
    already_active: entry('Already in use'),
    write_in_flight: entry('Waiting for the reply to the last request'),
    enter_identifier: entry('Enter an identifier'),
    no_profile: entry('Choose or create an account first'),
    starter: entry('The shipped starter profile stays generic; create your own account first'),
    invalid_value: entry('Fix the value above'),
    no_changes: entry('No change to save'),
    choose_profile: entry('Choose a profile'),
    draft: entry('A scan draft is not runnable; save it as a profile first'),
    not_checked: entry('Checking the profile'),
    problems: entry('This profile has problems; fix them before activating it'),
    validator_unavailable: entry("The profile check couldn't run"),
    unreadable: entry("This profile can't be read"),
    files_not_read: entry('The profiles have not been read yet'),
    enter_name: entry('Enter a name'),
    starter_name: entry('"default" is the shipped starter; choose another name'),
    choose_source: entry('Choose a profile to copy'),
    confirm_overwrite: entry('A profile with this name exists; tick Replace to overwrite it'),
    active_name: entry(
      'This is the active profile; choose another name',
      'Replacing the active profile in place would make an unchecked file the one automation uses. Write a new profile, then activate it after its check.',
    ),
    no_draft: entry('No scan draft to save'),
    none_active: entry('No profile is active'),
    unbind_unresolved: entry(
      'The previous unbind is unresolved; waiting for the configuration to confirm',
    ),
    context_unknown: entry('The emulator is not known yet'),
  },
  notSent: {
    unbind_unresolved: entry(
      'Not sent - the previous unbind is unresolved; waiting for the configuration to confirm',
    ),
    profile_write_in_flight: entry(
      'Not sent - another account or profile request is still waiting for its reply',
    ),
    context_unconfirmed: entry(
      "Not sent - the current emulator, account and profile couldn't be confirmed",
      'The page reads the configuration before each account or profile request; that read did not answer. Nothing was sent.',
    ),
    context_changed: entry(
      'Not sent - the account or profile changed; check the page and choose again',
    ),
    profile_not_valid: entry('Not activated - the check of that profile shows a problem'),
    destination_active: entry(
      'Not sent - that name is now the active profile; choose another name',
    ),
  },
  outcomes: {
    lead: entry('Last request:'),
    answeredAgo: (seconds: number) => made(ageAgo(seconds)),
    restored: entry('kept from before the page was reloaded'),
    sending: entry('Sending'),
    accepted: entry('Accepted, waiting for the configuration to confirm'),
    done: {
      account_select: entry('This emulator now uses the account'),
      account_create: entry('Account created and in use'),
      account_tier: entry('Saved'),
      profile_activate: entry('Activated - automation uses it from its next start'),
      profile_copy: entry('Copied - not active'),
      profile_promote: entry('Saved as a profile - not active'),
    },
    refused: entry(
      'Not done',
      'The server answered that it did not do this request; its reason is below.',
    ),
    contextChanged: entry(
      'Not saved: the account or profile changed',
      'Your draft stays with the account and profile you reviewed. Nothing is sent again.',
    ),
    contextLabels: {
      instance: entry('Current emulator'),
      account: entry('Current account'),
      profile: entry('Current profile'),
      serial: entry('Current serial'),
    },
    contextUnknown: entry('Not reported'),
    refusedCreate: entry(
      'Not created',
      'A failure after a file was written can leave a profile file behind, and the same identifier is then refused. The lists on this page are read again to show what exists now.',
    ),
    unknown: entry(
      'No confirmed reply - it may or may not have been done',
      'The original request may still finish. Nothing is sent again. The accounts and profiles are read again; an absent result keeps this request unresolved.',
    ),
    applied: entry('A later read shows the requested result'),
    /** F6-14 PE1: D9's later configuration confirms the unbind. */
    unbound: entry(
      'No profile is active - the scheduler runs its legacy rules from its next start',
    ),
    problems: (n: number) =>
      made(`Saved with ${plural(n, 'problem', 'problems')}; fix them before activating it`),
    abilitiesUnverified: entry('Abilities not verified'),
    tierTo: (tier: number) => made(`tier ${String(tier)}`),
    dismiss: entry('Dismiss'),
  },
} as const;
