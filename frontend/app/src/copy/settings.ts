import { entry, made } from './entry.ts';
import { ageAgo } from './dashboard/shell.ts';

function bound(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString('en-US') : String(value);
}

/**
 * The shared editor of one typed settings module (features/settings): its buttons, the
 * reasons a field's draft is not valid (judged against the server's own bounds, never
 * clamped), why a module is read-only, and what a save proved. Field labels, units and
 * descriptions are the server's (ServerValue), never this catalogue's.
 */
export const settings = {
  save: entry('Save'),
  discard: entry('Discard changes'),
  reconcile: entry('Use the read revision; keep my draft'),
  savedValue: entry('Saved:'),
  unlistedEmpty: entry('Empty value (not listed)'),
  low: entry('From'),
  high: entry('To'),
  x: entry('x'),
  y: entry('y'),
  on: entry('On'),
  aboutField: entry('this setting'),
  aboutModule: entry('these settings'),
  bounds: (min: number | null, max: number | null) =>
    made(
      min !== null && max !== null
        ? `${bound(min)} to ${bound(max)}`
        : min !== null
          ? `at least ${bound(min)}`
          : max !== null
            ? `at most ${bound(max)}`
            : 'any number',
    ),
  wholeOnly: entry('whole numbers'),
  unknownType: entry('This setting is shown as saved; this page cannot edit its kind of value'),
  invalid: {
    empty: entry('Enter a value'),
    not_a_number: entry('Use a number, like 12 or 2.5'),
    not_whole: entry('Use a whole number'),
    below_min: entry('Below the lowest allowed value'),
    above_max: entry('Above the highest allowed value'),
    reversed: entry('The first value must not be larger than the second'),
    unknown_option: entry('Choose one of the listed options'),
    unknown_type: entry('This page cannot edit this kind of value'),
  },
  readOnly: {
    starter: entry(
      'Read-only: the active profile is the shipped starter',
      'The starter profile stays generic. Create and activate your own profile on the Profiles page to edit these settings.',
    ),
    draft: entry(
      'Read-only: the active profile is a scan draft',
      'Save the scan draft as a profile and activate it on the Profiles page first.',
    ),
    no_profile: entry(
      'Read-only: no profile is active',
      'Choose or create an account and its profile on the Profiles page first.',
    ),
    server: entry(
      'Read-only for the active profile',
      "The server marks these settings read-only for this profile (for example another account's profile).",
    ),
  },
  reasons: {
    read_only: entry('Read-only for the active profile'),
    context_unknown: entry('The settings have not been read for this emulator and account yet'),
    other_context: entry(
      'The last read was for another emulator, account or profile; check again',
      'Its values are not shown or used here. Your unsaved edits for this emulator, account and profile are kept.',
    ),
    invalid_value: entry('Fix the marked values first'),
    no_changes: entry('No change to save'),
    save_pending: entry('Waiting until the last save is read back'),
    revision_changed: entry(
      'The settings revision changed or is missing; review the saved values and reconcile your draft',
    ),
  },
  outcomes: {
    lead: entry('Last save:'),
    answeredAgo: (seconds: number) => made(ageAgo(seconds)),
    restored: entry('kept from before the page was reloaded'),
    sending: entry('Saving'),
    saved: entry('Saved'),
    context_changed: entry('Not saved: the account or profile changed'),
    refused: entry(
      'Not saved',
      'The server answered that it did not save this request; its reason is below. Your edits are kept.',
    ),
    unknown: entry(
      'No confirmed reply - it may or may not have saved',
      'Nothing is sent again on its own. A later read settles it only if it holds the values sent. Otherwise, explicitly continue from the saved values as read before saving again; the earlier outcome stays unknown.',
    ),
    applied: entry('A later read shows it saved'),
    not_applied: entry('A later read shows it was not saved'),
    conflict_applied: entry(
      'The saved settings now have your changes',
      'The server refused this request as out of date, and a later read holds every value you sent.',
    ),
    problems: (n: number) => made(`${String(n)} ${n === 1 ? 'problem' : 'problems'}`),
    dismiss: entry('Dismiss'),
    acceptCurrent: entry('Continue from the saved values as read'),
    accepted: entry(
      ' Continuing from the saved values as read; the earlier outcome remains unknown.',
    ),
  },
} as const;
