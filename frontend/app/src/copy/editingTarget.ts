import { entry } from './entry.ts';

export const editingTarget = {
  automation: entry('Used by automation'),
  title: entry('Profile to edit'),
  picker: entry('Choose a profile to edit'),
  choose: entry('Choose a listed profile'),
  pick: entry('Edit this profile'),
  return: entry('Return editing to the active profile'),
  editing: entry('Editing:'),
  inactive: entry('(not the active profile)'),
  following: entry('Editing the active profile:'),
  active: entry('Active profile:'),
  none: entry('none'),
  change: entry('Change the editing profile in Profiles'),
  scope: entry('Choosing a profile to edit does not activate it or change automation.'),
  listFailed: entry('The profile list could not be read. Your editing profile is kept.'),
  retryList: entry('Retry profile list'),
  sourceFailed: entry(
    'This profile could not be read. Writes are unavailable until a successful read.',
  ),
  blocked: {
    no_profile: entry('No profile is active; choose a profile to edit'),
    files_unread: entry('The profile list must be read before editing this profile'),
    not_listed: entry('This profile is no longer listed for this account; choose another profile'),
    starter: entry('The shipped starter cannot be edited; copy it first'),
    draft: entry('A scan draft cannot be edited; save it as a profile first'),
  },
};
