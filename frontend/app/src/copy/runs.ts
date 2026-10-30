import { actions } from './actions.ts';
import { entry, made } from './entry.ts';
import { durationText, fractionalDurationText, serverDateText } from './format.ts';

function number(value: number): string {
  return String(value);
}

/** RL7 times are seconds since a server time (negative: still to come); null: no server clock. */
function when(since: number | null): string {
  if (since === null) return 'at a time not known';
  return since < 0 ? `in ${durationText(-since)}` : `${durationText(since)} ago`;
}
function dueText(since: number | null): string {
  if (since === null) return 'at a time not known';
  return since < 0 ? `in ${durationText(-since)}` : 'due now, waiting for the automation';
}
function stamp(at: number, since: number | null): string {
  return `${serverDateText(at)} (${when(since)})`;
}
/** A number's allowed range in words, without its unit (one rule for `range` and `bounds`). */
function rangeWords(min: number | null, max: number | null): string {
  return min !== null && max !== null
    ? `${number(min)} to ${number(max)}`
    : min !== null
      ? `at least ${number(min)}`
      : max !== null
        ? `at most ${number(max)}`
        : 'range not reported';
}
function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Catalogue entries of the Runs page (brief backend/logs/reviews/ui-pages/runs-brief.md). */
export const runs = {
  pageName: entry('Runs'),
  needsAttention: entry('Needs attention'),
  entrySections: {
    identity: entry('Identity'),
    behavior: entry('Entry behavior'),
    policies: entry('Policies'),
  },
  statistics: entry('Statistics'),
  alwaysOn: entry('Always on'),
  live: {
    routines: entry('Live routines'),
    title: entry('This battle'),
    scope: entry('Changes this battle only'),
    none: entry('No tracked battle - live settings appear here while automation watches a battle'),
    unavailable: entry("The tracked battle can't be read"),
    prepared: entry('The battle has not started yet; live settings open when it is active'),
    save: entry('Save changes to this battle'),
    check: entry('Check this battle'),
    /** A2-7 AC2 (gap G2): an item whose control paths the battle does not publish. */
    unaddressable: entry(
      "Its settings can't be changed here",
      'The battle runs more than one copy of this action and does not say which settings belong to this copy, so no change is offered.',
    ),
    /** A2-7 AC2 (brief section 6 rule 6): a setting whose kind of value this page does not know. */
    unrecognised: entry(
      'Not recognised',
      "The battle describes this setting with a kind of value this page doesn't know, so it is shown but can't be changed here.",
    ),
    boundsUnknown: entry(
      'Allowed ranges not read yet',
      'The ranges come from the saved settings of this battle run; the server checks every value either way.',
    ),
    overrides: {
      title: entry(
        'Changed for this battle',
        'These values differ from what the battle started with. Using the starting value again removes the change for this battle only.',
      ),
      reset: entry("Use the battle's starting value"),
      confirm: entry("Use the battle's starting value?"),
      effect: entry(
        'This battle goes back to the value it started with. That can differ from the saved settings below if they changed since the battle started. Nothing else changes.',
      ),
      send: entry('Use starting value'),
      cancel: entry('Cancel'),
      /** F6-11 RL2: every override of this battle in one request. */
      resetAll: (count: number) =>
        made(`Use the battle's starting values for all ${String(count)}`),
      confirmAll: entry("Use the battle's starting values for all of these?"),
      effectAll: entry(
        'This battle goes back to the values it started with for each setting listed. They can differ from the saved settings below if those changed since the battle started. Nothing else changes.',
      ),
      sendAll: entry('Use starting values'),
      shoppingPlan: entry('Shopping plan'),
    },
    marks: {
      changed: entry('Changed for this battle'),
      waiting: entry(
        'Waiting to apply',
        'The battle accepted this change; the automation has not taken it yet.',
      ),
    },
    /** F6-11 RL7 (spec F-062, F-063): what each routine reported in this battle. */
    details: {
      heading: entry('Battle details'),
      title: (routine: string) => made(`${routine}: battle details`),
      lastKnown: entry(
        'Last known values',
        'The battle report is not fresh, so these values may have changed since.',
      ),
      inspect: entry('Events of this battle on Runtime'),
      raw: entry('Reported values', 'What the battle reported for this routine, exactly as sent.'),
      offer: entry('Offer'),
      visibility: {
        visible: entry('Seen on the last battle screen'),
        absent: entry('Not on the last battle screen'),
        unknown: entry('Not known'),
      },
      oldObservation: entry('the last look at the screen is too old'),
      firstSeen: entry('First seen'),
      notSeen: entry('Not seen yet'),
      appeared: entry(
        'Appeared',
        'The offer appeared after the earlier look and was seen at the later one; the first sighting is not the moment it appeared.',
      ),
      appearedUnknown: entry('Not known; the first sighting is not when it appeared'),
      appearedBetween: (
        afterAt: number,
        afterSince: number | null,
        byAt: number,
        bySince: number | null,
        /** The appearance interval as reported, to a tenth of a second (legacy run-workspace.js:264). */
        gapSec: number,
      ) =>
        made(
          `After ${stamp(afterAt, afterSince)}, seen by ${stamp(byAt, bySince)}; ${fractionalDurationText(gapSec)} between looks`,
        ),
      lastLook: entry('Last look at the battle'),
      noLook: entry('No look recorded'),
      collection: entry('Collection'),
      collectionOff: entry('Collection off'),
      responseWindow: (due: number | null, closes: number | null) =>
        made(`Response ${dueText(due)}; window closes ${dueText(closes)}`),
      windowCloses: (closes: number | null) => made(`Window closes ${dueText(closes)}`),
      nextWindow: (due: number | null) => made(`Next window ${dueText(due)}`),
      nextWindowUnknown: entry('Next window not reported'),
      nextLook: entry('Next look'),
      due: (due: number | null) => made(capital(dueText(due))),
      /** A reported time: the server's date and how long ago (review F6-11-R1-03). */
      at: (at: number, since: number | null) => made(stamp(at, since)),
      notReported: entry('Not reported'),
      failedAttempt: entry('Latest failed attempt'),
      refusal: entry('Latest refusal'),
      noneRecorded: entry('None recorded'),
      counts: entry('Counts'),
      attempts: entry('Attempts'),
      confirmed: entry('Confirmed'),
      skipped: entry('Skipped'),
      windowsMissed: entry('Windows missed'),
      timing: entry('Last timing'),
      scheduleWait: entry('Schedule wait'),
      responseDelay: entry('Response delay'),
      verification: entry('Verification'),
      seconds: (value: number | null) =>
        made(value === null ? 'Not reported' : `${String(value)} s`),
    },
  },
  saved: {
    routines: entry('Saved routines'),
    title: entry('Saved settings'),
    scope: entry('Used by future battles'),
    picker: entry('Run'),
    unnamedRun: entry('Run without a name'),
    noRuns: entry('No runs to show yet'),
    combo: entry('Day plans choose a run for each block; edit them on Schedule'),
    notListed: entry(
      'This run is not in the list of runs of the active profile',
      'The link named this run. The page shows its saved settings only if the server knows it; it never opens another run instead.',
    ),
    reading: entry('Reading the saved settings'),
    catalogueUnavailable: entry(
      'The run list could not be read. Check it again before editing saved settings.',
    ),
    checkRuns: entry('Check runs'),
    unavailable: entry("The saved settings can't be read"),
    save: entry('Save run settings'),
    check: entry('Check saved values'),
    prepare: entry('Prepare runs on the Recognition page'),
  },
  discard: entry('Discard'),
  readAge: (seconds: number) => made(`read ${durationText(seconds)} ago`),
  seconds: entry('s'),
  notReported: entry('Not reported'),
  range: (min: number | null, max: number | null) =>
    made(`${rangeWords(min, max)}${min !== null || max !== null ? ' s' : ''}`),
  /**
   * A2-7 AC2: the bounds of a number whose unit is not seconds; when its unit (server text)
   * follows, the bounds end with the space before it.
   */
  bounds: (min: number | null, max: number | null, unitFollows: boolean) =>
    made(`${rangeWords(min, max)}${unitFollows && (min !== null || max !== null) ? ' ' : ''}`),
  duration: (seconds: number) => made(`= ${durationText(seconds)}`),
  edited: entry('Edited, not saved'),
  invalid: {
    empty: entry('Enter a number'),
    not_a_number: entry('Use digits only, like 30 or 2.5'),
    below_min: entry('Below the allowed range'),
    above_max: entry('Above the allowed range'),
    not_editable: entry("This setting can't be changed here"),
  },
  changes: (count: number) =>
    made(count === 1 ? '1 change not saved' : `${String(count)} changes not saved`),
  reasons: {
    context_unknown: entry('Waiting for the emulator, account and profile to be read.'),
    run_not_read: entry("The run's saved settings have not been read yet"),
    read_other_context: entry('These settings were read for another emulator or profile'),
    revision_unknown: entry('The version of these settings is not known; check them again'),
    save_in_flight: entry('Saving'),
    save_unresolved: entry('An earlier save has no answer yet; check the values first'),
    refreshing: entry('Reading the values the last save left'),
    invalid_value: entry('Fix the values marked above'),
    no_changes: entry('No changes to save'),
    no_battle: entry('No battle is tracked'),
    battle_not_active: entry('The battle has not started yet'),
    report_stale: entry('Waiting for a fresh battle report'),
    change_pending: entry('Waiting for the last change to apply'),
    /** A2-7 AC2: the reset of a field the descriptor does not mark nullable. */
    not_nullable: entry('This setting has no starting value to go back to'),
    /** A2-7 AC2 (root decision Q2): core/actions actionControlsBlocked codes. */
    ...actions.blocked,
  },
  outcomes: {
    lead: entry('Last request:'),
    sending: entry('Sending'),
    saved: entry('Saved'),
    savedPartly: entry(
      'Saved - not every value was kept',
      'The server saved the run with other values for the fields still marked "Edited, not saved"; those fields keep what you typed.',
    ),
    notSaved: entry('Not saved'),
    unknownSave: entry(
      'No reply - it may or may not have saved',
      'Nothing is sent again. Check saved values reads the run: a read showing your changes confirms the save; a read without them cannot prove it failed, because it may still be written. You may then continue from the saved values as read.',
    ),
    unconfirmedSave: entry(
      "The reply didn't confirm the save - it may or may not have saved",
      'Nothing is sent again. Check saved values reads the run: a read showing your changes confirms the save; a read without them cannot prove it failed, because it may still be written. You may then continue from the saved values as read.',
    ),
    applied: entry('The saved settings now have your changes'),
    acceptCurrent: entry(
      'Continue from the saved values as read',
      'The last read does not show your changes, but that does not prove the save failed: it may still be written. Continuing lets you save again from the values the read shows; this line stays unknown and nothing is sent again.',
    ),
    accepted: entry(
      'You continued from the saved values as read; this save is still not confirmed',
    ),
    notApplied: entry('Not all of your changes are in the saved settings'),
    liveRecorded: entry('Sent to this battle - waiting to apply'),
    liveNotRecorded: entry('Not sent - nothing was recorded'),
    liveUnknown: entry(
      'No reply - it may have been recorded',
      'Send same request again sends exactly the same change with the same key; the battle answers with what it recorded.',
    ),
    liveUnconfirmed: entry(
      "The reply didn't confirm the change - it may have been recorded",
      'Send same request again sends exactly the same change with the same key; the battle answers with what it recorded. Check this battle reads it.',
    ),
    liveSettled: entry('A later read lists this change'),
    liveApplied: entry('Applied to this battle'),
    liveNotApplied: entry(
      'Not applied - this battle rejected the change or it expired',
      'The battle recorded the change, then reported that it did not apply it, for example because the battle ended first. Nothing is sent again.',
    ),
    notSavedChanged: entry(
      'Not saved - the saved run changed since you read it',
      'Someone else saved this run first. Your values stay in the fields, compared with the newest saved values; Save sends them again only when you click it.',
    ),
    earlier: entry('Change to another battle:'),
    earlierRecorded: entry('That battle recorded the change'),
    checkEarlier: entry('Check that battle'),
    attemptRefused: entry(
      'Sending it again was refused - this does not show whether the first request was recorded',
      'The server refused the repeated request before looking for the first one (for example, while another action was in progress). The first request may still have been recorded: send the same request again, or check the battle.',
    ),
    resend: entry('Send same request again'),
    dismiss: entry('Dismiss'),
    answeredAgo: (seconds: number) => made(`${durationText(seconds)} ago`),
  },
} as const;
