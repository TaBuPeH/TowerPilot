import { entry, made } from './entry.ts';

/** SQ9/SQ10 shared policy and legacy editor wording. */
export const shoppingLibrary = {
  title: entry('Shared shopping policies'),
  manage: entry('Manage policies'),
  more: entry('Show more policies'),
  profile: entry('Active profile'),
  scope: entry(
    'Shared saves affect these runs in future battles; the current battle keeps its queue.',
  ),
  usage: entry('Referenced by'),
  unused: entry('No runs reference this policy.'),
  binding: entry('Bind a shopping policy in the run entry editor'),
  reading: entry('Reading shared shopping policies…'),
  unavailable: entry(
    'The latest profile read is unavailable. Drafts are kept; check the profile before saving.',
  ),
  empty: entry('No shared shopping policies in this profile.'),
  check: entry('Check shared policies'),
  create: entry('Create a shared queue'),
  createTitle: entry('New shared shopping queue'),
  copyTitle: entry('Copy shared shopping policy'),
  copy: entry('Copy policy'),
  id: entry('New policy ID'),
  save: entry('Save shared policy'),
  edit: entry('Edit shared policy'),
  discard: entry('Discard policy draft'),
  remove: entry('Delete policy'),
  confirm: entry('Delete this unused policy?'),
  confirmNote: entry(
    'This removes the shared policy from the active profile. Its draft will be discarded only after confirmation from the server.',
  ),
  confirmDelete: entry('Confirm policy deletion'),
  cancel: entry('Cancel deletion'),
  reconcile: entry('Use the latest revision for this draft'),
  reconcileNote: entry(
    'Review the latest policy below. Continuing keeps your full draft for an explicit replacement; it sends nothing and leaves any unknown request recorded.',
  ),
  observed: entry('Latest policy read'),
  legacyWarning: entry('Legacy shopping has no cash reserve protection.'),
  bare: entry(
    'This policy is a bare directive list. It has no master switch or label; saving preserves its list shape.',
  ),
  enabled: entry('Policy enabled'),
  label: entry('Policy label'),
  lineEnabled: entry('Directive enabled'),
  tab: entry('Shop tab'),
  stats: entry('Stats'),
  statsHint: entry('Choose every stat this directive should shop for.'),
  mode: entry('Shopping mode'),
  choose: entry('Choose a value'),
  clicks: entry('Clicks'),
  clicksHint: entry('Required only for clicks mode. The server validates the integer and range.'),
  clicksEmpty: entry('Enter clicks for clicks mode.'),
  clicksNumber: entry('Enter a number; the draft is kept as typed.'),
  add: entry('Add directive'),
  removeLine: entry('Remove directive'),
  up: entry('Up'),
  down: entry('Down'),
  first: entry('Already first.'),
  last: entry('Already last.'),
  line: (position: number) => made(`Directive ${position}`),
  moved: (position: number) => made(`Directive moved to position ${position}.`),
  reasons: {
    unavailable: entry('Confirm the active profile with a successful read before saving.'),
    pending: entry('Wait for the current save and a fresh profile read.'),
    unknown: entry(
      'A profile save is Unknown. Check shared policies; only a matching read or your explicit decision resolves the fence.',
    ),
    revision: entry(
      'The profile revision changed. Check shared policies and review the latest value before continuing with this draft.',
    ),
    invalid: entry(
      'Complete the fields marked invalid; legacy choices also require the shopping vocabulary.',
    ),
    unchanged: entry('Edit this policy before saving.'),
    used: entry(
      'This policy is referenced by runs. Change their shopping bindings in the run entry editor before deleting it.',
    ),
    id: entry('Enter an unused policy ID.'),
    creation: entry('Save or discard the new policy draft before starting another.'),
  },
  notSent: {
    context_unconfirmed: entry(
      "Not sent: the current emulator, account and profile couldn't be confirmed. Your policy draft is kept.",
    ),
    context_changed: entry(
      'Not sent: the account or profile changed. Return to the original context to review its policy draft.',
    ),
    collection_write_in_flight: entry(
      'Not sent: another profile or settings save is still waiting for its reply.',
    ),
  },
};
