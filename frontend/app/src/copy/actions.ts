import { entry } from './entry.ts';

/**
 * A2-7: the names of timed actions and gates and the labels of their fields, one table each,
 * read through core/actions.ts actionName and fieldLabel (Dashboard This battle, Runs editors
 * and battle details). Until the projection's labels are shown (C1, C2) these are the words.
 */
export const actions = {
  /**
   * A2-7 AC5 (root decision Q2): the one sentence per core/actions `actionControlsBlocked` reason
   * (`ACTION_CONTROL_BLOCKS`), shared by every control the reason closes: This battle's Run now
   * and Try once, and the live editor's sends. Draft typing stays open.
   */
  blocked: {
    action_report_old: entry(
      'The action readings are old; Run now and changes wait for a current reading',
      'The battle report keeps arriving, but the readings of its timed actions stopped advancing. Check this battle reads it again.',
    ),
    action_report_time_invalid: entry(
      "The action readings carry a time that can't be right; check this battle",
    ),
    action_schema_newer: entry(
      'A newer version wrote these action readings; Run now and changes are not offered',
    ),
    action_context_mismatch: entry(
      'These action readings name another emulator, account or profile; Run now and changes are not offered',
    ),
    battle_report_old: entry('Waiting for a fresh battle report'),
    battle_time_invalid: entry("The battle report's time can't be read; check this battle"),
  },
  names: {
    ad_gems: entry('Ad gems'),
    floating_gems: entry('Floating gems'),
    shopping: entry('Shopping'),
    rewards: entry('Rewards'),
    abilities: entry('Abilities'),
    rescue: entry('Rescue'),
    repeat: entry('Repeat'),
  },
  otherName: entry('Other routine'),
  fields: {
    enabled: entry('On'),
    interval_sec: entry('Every'),
    window_sec: entry('Window', 'How long one attempt may take.'),
    tap_interval_sec: entry('Tap every', 'Time between taps on a floating gem.'),
  },
  otherField: entry('Setting'),
  /** A2-7 AC4: the words for an execution's recorded effect, keyed by the code the server records. */
  effects: {
    confirmed: entry('Effect confirmed'),
    unconfirmed: entry('Finished, effect not confirmed'),
    /** A battle ad whose only proof is that the offer was gone after the tap; no balance was read. */
    offer_gone: entry('Offer gone after tap'),
    unknown: entry('Effect unknown'),
    none: entry('No game effect'),
  },
  /** A2-7 AC4: the words for an execution's recorded result, keyed by the code the server records. */
  results: {
    succeeded: entry('Succeeded'),
    no_op: entry('Checked, nothing to do'),
    failed: entry('Failed'),
    refused: entry('Refused'),
    interrupted: entry('Stopped before it finished'),
    cancelled: entry('Cancelled'),
    unknown: entry('Unknown'),
  },
};
