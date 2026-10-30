import { entry } from './entry.ts';

export const shoppingSaved = {
  title: entry('Shopping queue'),
  edit: entry('Edit the saved queue'),
  routines: entry('Shopping on/off and interval are edited in the run controls above.'),
  unsupported: entry(
    'This run does not support a shopping queue. The server does not offer queue editing for this run.',
  ),
  unavailable: entry(
    'Read this run in the current account and profile before editing its shopping queue.',
  ),
  create: entry('Create a queue'),
  createHint: entry(
    'Choose a cash reserve, purchase pattern and cadence, then Save. Creating a draft sends nothing.',
  ),
  conversion: entry('Review legacy conversion'),
  conversionWarning: entry(
    'Conversion changes behaviour. Review every warning and entry, including disabled and excluded upgrades. Using this queue only stages a draft.',
  ),
  master: entry('Shopping in the reviewed queue'),
  enabled: entry('On'),
  disabled: entry('Off'),
  leftOut: entry('Left out of queue'),
  reserveUnset: entry('Not chosen; enter a cash reserve below before using this queue.'),
  reserveFirst: entry('Enter a cash reserve first (0 allows spending all cash).'),
  interval: entry('Interval in seconds'),
  intervalUnknown: entry('Read the saved shopping interval before saving this queue.'),
  unlimited: entry('Unlimited'),
  serverDefault: entry('Server default'),
  noLastWave: entry('No last wave'),
  excluded: entry('Excluded upgrades'),
  noExclusions: entry('No excluded upgrades'),
  reviewed: entry('Use reviewed queue'),
  save: entry('Save queue'),
  discard: entry('Discard queue draft'),
  nothing: entry('There is no queue draft to discard.'),
  invalid: entry(
    'Enter a number for cash reserve and choose a purchase pattern and cadence. Correct any fields marked invalid before saving.',
  ),
  unchanged: entry('The queue has no unsaved changes.'),
};
