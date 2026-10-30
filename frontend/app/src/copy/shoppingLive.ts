import { entry } from './entry.ts';
import { runs } from './runs.ts';
export const shoppingLive = {
  title: entry('Queue for this battle'),
  edit: entry("Edit this battle's queue"),
  scope: entry('The queue changes after automation applies the saved request.'),
  save: entry('Save queue for this battle'),
  discard: entry('Discard queue edits'),
  dashboard: entry('Try once in Dashboard This battle'),
  check: entry('Check this battle'),
  overrides: entry(
    'Routine overrides keep precedence over this queue. The shopping switch and interval above remain unchanged.',
  ),
  reasons: {
    unsupported: entry(
      'Live queue editing needs a version 2 queue and shopping plan controls advertised by this automation. Create or convert a saved queue for a later battle.',
    ),
    no_battle: entry('A tracked battle is required'),
    context_unknown: runs.reasons.context_unknown,
    battle_not_active: entry('An active tracked battle is required'),
    report_stale: entry('Read a fresh battle report before saving'),
    action_report_old: runs.reasons.action_report_old,
    action_report_time_invalid: runs.reasons.action_report_time_invalid,
    action_schema_newer: runs.reasons.action_schema_newer,
    action_context_mismatch: runs.reasons.action_context_mismatch,
    battle_report_old: runs.reasons.battle_report_old,
    battle_time_invalid: runs.reasons.battle_time_invalid,
    change_pending: entry('Waiting for the automation to apply the previous change'),
    revision_unknown: entry('The battle control revision is unknown'),
    save_in_flight: entry('A live change is sending'),
    save_unresolved: entry(
      'An earlier live change is Unknown. Check this battle or send that same request again.',
    ),
    refreshing: entry('Read this battle after the previous change'),
    invalid_value: entry('Review the queue fields before saving'),
    no_changes: entry('No queue changes to save'),
  },
};
