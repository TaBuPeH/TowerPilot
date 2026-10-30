import { lateAnswerPossible, type IntentRecord, type ReadSettlement } from './types.ts';

/** Draft lifetime follows the answer, not just evidence of matching server values. */
export function draftSettlement(
  record: IntentRecord,
  settlement: ReadSettlement | null = record.readSettlement,
): 'pending' | 'refused' | 'applied' | 'unknown' {
  if (lateAnswerPossible(record)) return 'pending';
  if (
    record.phase === 'refused' &&
    (record.kind === 'profile_patch' || record.kind === 'schedule_save')
  )
    return 'refused';
  return record.phase === 'confirmed' || settlement === 'applied' || settlement === 'recorded'
    ? 'applied'
    : 'unknown';
}
