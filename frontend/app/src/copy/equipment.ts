import { entry, made } from './entry.ts';

export const equipment = {
  space: made(' '),
  pageName: entry('Equipment'),
  intro: entry(
    'Saving changes configuration only; it does not equip or verify anything in the game.',
  ),
  target: entry('Saved equipment target'),
  instance: entry('Emulator'),
  profile: entry('Profile'),
  state: entry('Selection state'),
  selected: entry('Selected'),
  fullRevision: entry('Full value'),
  tournamentAccount: entry('Tournament settings scope'),
  tournamentLegacyScope: entry(
    'Legacy installation settings; separate from the saved-loadout target below.',
  ),
  tournamentBoundScope: entry(
    'Applies to this bound account; saved loadouts below also depend on the selected profile.',
  ),
  more: entry('Show more loadouts'),
  review: entry('Review and validation'),
  pendingCancel: entry('Wait for the pending save before discarding edits.'),
  cleanCancel: entry('There are no unsaved edits to discard.'),
  reconcileBlocked: entry(
    'Read saved result successfully and wait for any pending save before keeping this draft.',
  ),
  unknown: entry('Not reported'),
  noTarget: entry('Select an emulator and profile to read its saved loadouts.'),
  scope: entry('Save scope: one named loadout in this account’s configuration.'),
  limit: entry(
    'Save checks the current emulator, account, profile and connection, then sends that target and the configuration revision for the server to check before writing.',
  ),
  empty: entry('No saved loadouts. Creating loadouts for this account is not available yet.'),
  reading: entry('Reading saved equipment…'),
  readError: entry(
    'Saved equipment could not be read for this target. Kept drafts are unchanged. Read saved result before saving.',
  ),
  stale: entry(
    'This read is more than 30 seconds old. Read saved result to check the target and revision before saving.',
  ),
  read: entry('Read saved result'),
  list: entry('Saved loadouts'),
  editor: entry('Selected loadout'),
  consumers: entry('Used by saved runs'),
  unused: entry('None'),
  missingSelection: entry('Missing discovery'),
  missingTarget: entry(
    'This loadout is missing from the latest saved configuration. Its draft stays here; saving is blocked.',
  ),
  placeholder: entry('Placeholder — equips nothing'),
  untouched: entry('All categories left untouched'),
  manual: entry('Manual legacy equipment'),
  global: entry('Global preset'),
  category: entry('Category presets'),
  mode: entry('Selection mode'),
  global_preset: entry('Global preset selection'),
  cards: entry('Cards'),
  module_preset: entry('Modules'),
  guardian_preset: entry('Guardians'),
  workshop_preset: entry('Workshop'),
  bot_preset: entry('Bots'),
  none: entry('Leave untouched'),
  leaveUntouched: entry(
    'Leave untouched keeps the current game selection; it does not select None.',
  ),
  chooseGlobal: entry('Choose a discovered global preset'),
  missing: entry(
    'A saved selection is missing from this profile’s discoveries. It stays selected; repair discovery in Recognition.',
  ),
  recognition: entry('Open Recognition'),
  noChoice: entry('Choose at least one preset to save this loadout.'),
  manualWarning: entry(
    'Manual equipment selections are preserved here. Switching mode removes the legacy fields listed below.',
  ),
  help: entry('equipment selection'),
  helpGlobal: entry(
    'A global preset selects the game’s global picker at battle entry. Its contents are not inspected or verified here.',
  ),
  helpNone: entry('Blank category selections are omitted from the saved loadout.'),
  helpManual: entry('Legacy manual selections stay read-only until you review a conversion.'),
  compare: entry('Review before and after'),
  before: entry('Original saved body'),
  latest: entry('Latest saved body'),
  after: entry('Proposed saved body'),
  removed: entry('Fields removed by this selection'),
  reviewConversion: entry('I reviewed the removed manual fields'),
  conversionNeeded: entry('Review the removed manual fields before saving this conversion.'),
  revision: entry('Latest configuration revision'),
  base: entry('Draft’s starting revision'),
  save: entry('Save selection'),
  cancel: entry('Cancel edits'),
  cancelTitle: entry('Discard this loadout’s edits?'),
  cancelBody: entry(
    'This removes only this loadout’s unsaved edits. The original saved body stays intact.',
  ),
  keep: entry('Keep editing'),
  dirty: entry('Unsaved selection'),
  clean: entry('Saved selection'),
  blocked: entry(
    'Save is unavailable. Check selections, target, revision and any unresolved result; read saved result when needed.',
  ),
  sending: entry('Saving this selection once… New edits stay in the draft.'),
  checking: entry('Checking the current account and profile… Nothing has been sent.'),
  contextChanged: entry('Not saved: the account or profile changed'),
  selectionChanged: entry(
    'Not saved: another loadout was selected during the check. Your draft is kept with this loadout.',
  ),
  draftChanged: entry(
    'Not saved: the editor changed during the check. Review the kept draft before saving again.',
  ),
  contextUnconfirmed: entry(
    'Not saved: the current account or profile could not be checked. Your draft is kept.',
  ),
  currentContext: entry('Current context reported by the server'),
  saved: entry('Selection saved in configuration. No game equipment was applied or verified.'),
  unknownResult: entry(
    'The save outcome is unknown. Do not send it again. Read saved result for the original target and loadout.',
  ),
  acceptedCurrent: entry(
    'Continuing from the saved values as read. The earlier save outcome remains unknown. Saving requires a separate click.',
  ),
  refused: entry('The backend refused this save. Your draft is kept.'),
  conflict: entry(
    'The saved configuration changed. Read saved result and review before saving again.',
  ),
  readApplied: entry(
    'The saved read contains the submitted selection. Newer edits remain unsaved.',
  ),
  readUnchanged: entry(
    'The saved read still has the original body and revision. Your draft is kept.',
  ),
  result: entry('Result for this loadout and target'),
  reconcile: entry('Keep draft against this read revision'),
  reconcileHelp: entry(
    'Review the latest saved body and your draft. This keeps your choices against the revision just read; saving still requires a separate click.',
  ),
  warnings: entry('Backend warnings and validation'),
  earlierValidation: entry(
    'Profile validation below is from a read before this save. Read saved result to refresh it.',
  ),
};
