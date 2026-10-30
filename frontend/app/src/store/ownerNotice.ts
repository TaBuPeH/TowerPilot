import {
  assessRefusalNotice,
  classifyRefusal,
  type RefusalClass,
  type RefusalNoticeAssessment,
} from '../core/outcomes/refusal.ts';
import { statusReadOf } from './reads.ts';
import type { ControlFacts } from './selectors/controlFacts.ts';
import type { DashboardState, IntentView } from './types.ts';

/**
 * The shared owner notice (spec 7.5; S-049, S-070 to S-074). Every write's
 * refusal goes through ONE handler that classifies the body by its shape
 * (core `classifyRefusal`), whatever route answered: the newest owner notice
 * of the current context is the page's notice. Its stopped, free and "you can
 * try again" claims come only from a later D-4 read (core
 * `assessRefusalNotice`), and it offers exactly the server's recovery, never a
 * retry of the refused request (S-070). Dismissing it hides the line only:
 * the operation it names keeps its Stop in the Automation slot
 * (`noticedOperations`, INV-3) until a later read proves that operation stopped.
 */
export type NoticeClass = 'operation_owner' | 'process_owner' | 'lease_holder' | 'owner_unreadable';

export interface OwnerNoticeFacts {
  readonly record: IntentView;
  readonly noticeClass: NoticeClass;
  readonly notice: RefusalClass;
  /** The judgement of the latest status read started after the notice, or null when there is none. */
  readonly assessment: RefusalNoticeAssessment | null;
}

function noticeClassOf(notice: RefusalClass): NoticeClass | null {
  switch (notice.kind) {
    case 'operation_owner':
    case 'process_owner':
    case 'lease_holder':
    case 'owner_unreadable':
      return notice.kind;
    default:
      return null;
  }
}

export function ownerNotice(state: DashboardState, facts: ControlFacts): OwnerNoticeFacts | null {
  let newest: {
    readonly record: IntentView;
    readonly notice: RefusalClass;
    readonly noticeClass: NoticeClass;
  } | null = null;
  for (const record of facts.records) {
    if (record.answer === null || record.answerSeq === null || record.dismissed) continue;
    if (record.answer.status < 400 || record.answer.status > 499) continue;
    const notice = classifyRefusal(record.answer.body, facts.claims);
    const noticeClass = noticeClassOf(notice);
    if (noticeClass === null) continue;
    if (newest === null || record.answerSeq > (newest.record.answerSeq ?? 0)) {
      newest = { record, notice, noticeClass };
    }
  }
  if (newest === null) return null;
  const latest = state.reads.status.latest;
  const after = newest.record.answerSeq ?? 0;
  return {
    record: newest.record,
    noticeClass: newest.noticeClass,
    notice: newest.notice,
    assessment:
      latest === null || latest.startedSeq <= after
        ? null
        : assessRefusalNotice(
            newest.notice,
            statusReadOf(latest),
            after,
            facts.claims,
            facts.leaseHolders,
          ),
  };
}

/**
 * INV-3 and OD-10: every operation an owner notice of this context named (newest first, each
 * once), dismissed notices included, until a status read started after that notice proves it
 * stopped (core `assessRefusalNotice`, D-4). The Automation slot offers a Stop for each, so
 * dismissing the notice never takes that Stop away. Only records made in this named context
 * count (their answers are its evidence).
 */
export function noticedOperations(state: DashboardState, facts: ControlFacts): readonly string[] {
  const latest = state.reads.status.latest;
  const found: string[] = [];
  const records = [...facts.bound].sort((a, b) => (b.answerSeq ?? 0) - (a.answerSeq ?? 0));
  for (const record of records) {
    if (record.answer === null || record.answerSeq === null) continue;
    if (record.answer.status < 400 || record.answer.status > 499) continue;
    const notice = classifyRefusal(record.answer.body, facts.claims);
    if (notice.kind !== 'operation_owner' || found.includes(notice.operationId)) continue;
    const stopped =
      latest !== null &&
      latest.startedSeq > record.answerSeq &&
      assessRefusalNotice(
        notice,
        statusReadOf(latest),
        record.answerSeq,
        facts.claims,
        facts.leaseHolders,
      ).stopped.kind === 'known';
    if (!stopped) found.push(notice.operationId);
  }
  return found;
}
