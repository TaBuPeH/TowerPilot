import { entry } from './entry.ts';

const idTaken = entry('That run identifier is already taken. Choose another; your draft is kept.');

export const runsAdd = {
  title: entry('Add a run'),
  scope: entry('Adding never starts a run or changes the schedule.'),
  recipe: entry('Recipe'),
  runId: entry('Run identifier'),
  choose: entry('Choose equipment'),
  defaults: entry('Use recipe default'),
  asIs: entry('Keep current equipment'),
  save: entry('Save new run'),
  check: entry('Check profile'),
  discard: entry('Discard add-run draft'),
  profiles: entry('Open Profiles'),
  accept: entry('Continue from the current state'),
  notAddedAgain: entry(
    'Not added again. The earlier add is still Unknown until a profile read settles it.',
  ),
  accepted: entry('Continuing from the current state. The earlier add is still Unknown.'),
  fields: {
    label: entry('New run label'),
    tier: entry('Tier'),
    loadout: entry('Equipment'),
    enable_uw: entry('Include Ultimate Weapon rules'),
    enable_rescue: entry('Include rescue rules'),
    count: entry('Run count'),
    gem_entry_max: entry('Tournament gem entry cap'),
    dissonant_tab: entry('Workshop tab to disable'),
    perk_bans: entry('Perk bans (one per line)'),
  },
  tabs: {
    attack: entry('Attack'),
    defense: entry('Defense'),
    utility: entry('Utility'),
    ultimate_weapons: entry('Ultimate Weapons'),
  },
  reasons: {
    enter_id: entry('Enter a run identifier.'),
    bad_id: entry('Use 1 to 64 lowercase letters, numbers or underscores, starting with a letter.'),
    id_taken: idTaken,
    unavailable: entry(
      'Read the recipes, account equipment and active profile before adding a run.',
    ),
    invalid_options: entry(
      'Enter a label and whole-number choices. Choose account equipment for this recipe.',
    ),
    pending: entry(
      'Wait for this add to finish, or resolve its Unknown outcome before saving again.',
    ),
  },
  outcomes: {
    sending: entry('Saving new run…'),
    confirmed: entry('New run saved.'),
    refused: entry('Run was not added. Your draft is kept.'),
    unconfirmed: entry(
      'Unknown: the add may have been saved. Check the profile before continuing. Your draft is kept.',
    ),
  },
  refusals: {
    invalid_request: entry('The add request was refused. Review the choices before trying again.'),
    id_taken: idTaken,
    profile_unavailable: entry('The profile is unavailable for this add. Check it in Profiles.'),
    recipe_refused: entry('The recipe or its choices were refused. Your draft is kept.'),
    validation_failed: entry('The new run did not pass profile validation. Your draft is kept.'),
    write_failed_nothing_saved: entry('The profile could not be written. Nothing was saved.'),
    revision_conflict: entry('The saved profile changed. Check the profile before saving again.'),
    context_changed: entry('The active context changed. Check the profile before saving again.'),
  },
};
