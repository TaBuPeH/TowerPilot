import { entry } from './entry.ts';
import { settingsList } from './settingsList.ts';

export const configEquipment = {
  tournament: entry('Standing tournament deck changes'),
  tournamentIntro: entry(
    'An empty list leaves the deck unchanged.',
    'Card template names are applied in order at tournament entry.',
  ),
  account: entry('Account'),
  legacy: entry('Legacy installation'),
  check: entry('Read tournament settings'),
  unavailable: entry(
    'Tournament settings have not been read for this context. Kept drafts stay with their original account.',
  ),
  stale: entry(
    'The latest tournament settings read failed. Read again before saving; your draft is kept.',
  ),
  entry: settingsList.entry,
  restore: entry('Restore after this block'),
  cards: entry('Restore cards preset'),
  modules: entry('Restore modules'),
  module: entry('Module name'),
  slot: entry('Slot'),
  addModule: entry('Add restore module'),
  removeModule: entry('Remove restore module'),
  restoreIntro: entry(
    'At battle entry, hand-equipping can permanently rewrite the active game preset.',
  ),
  unused: entry(
    'Restore changes cannot be saved: use this loadout in a run of the active profile first.',
  ),
  noProfile: entry(
    'Restore changes cannot be saved: activate a profile that uses this loadout first.',
  ),
  invalid: entry(
    'Use exactly one non-blank module name and slot per restore row. Replace malformed saved rows by removing them and adding a row.',
  ),
  missingCards: entry(
    'The saved restore preset is not in the profile’s card-preset choices. It is kept as saved.',
  ),
};
