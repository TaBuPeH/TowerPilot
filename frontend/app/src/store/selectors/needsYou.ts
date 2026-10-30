import {
  finishSetupNeed,
  guardImpact,
  handoffTarget,
  needsView,
  setupOutcome,
  statusNeed,
  type GuardImpact,
} from '../../core/needs.ts';
import { own, ownPath } from '../../core/read.ts';
import { otherRunsMissingImages, selectRun } from '../../core/readiness.ts';
import { fromServer, type ServerText } from '../../copy/server.ts';
import type { HandoffCommand } from '../commands.ts';
import { answeredBody, contextReadOf, latestRead, readAgeSec } from '../reads.ts';
import type { DashboardState, ResourceId } from '../types.ts';
import { chipVM, type ChipVM } from '../viewModels.ts';

export type HandoffTargetVM = Omit<HandoffCommand, 'attestation'>;

function handoffTargetVM(
  target: ReturnType<typeof handoffTarget>,
  epoch: number,
): HandoffTargetVM | null {
  return target === null ? null : { ...target, epoch };
}

export interface GuardImpactVM {
  readonly start: GuardImpact['start'];
  readonly collection: GuardImpact['collection'];
  readonly commonBlock: boolean;
  readonly setupMessage: ServerText<'message'> | null;
  readonly collectionMessage: ServerText<'message'> | null;
}

export interface CheckedReadVM {
  readonly source: ResourceId;
  readonly ageSec: number | null;
  readonly outcome: 'answered' | 'failed' | 'not_read';
}

export interface NeedItemVM {
  readonly code:
    'launch_guard' | 'setup_outcome' | 'status_need' | 'finish_setup' | 'other_runs_images';
  readonly chip: ChipVM | null;
  readonly source: ResourceId;
  readonly ageSec: number | null;
  readonly sources: readonly CheckedReadVM[];
  readonly impact: GuardImpactVM | null;
  readonly handoffTarget: HandoffTargetVM | null;
  readonly handoffCommand: HandoffCommand | null;
  readonly handoffBattleId: ServerText<'id'> | null;
  readonly lastReadAgeSec: number | null;
  readonly setupStep: ReturnType<typeof finishSetupNeed>;
  readonly imageCount: number | null;
  readonly operationId: ServerText<'id'> | null;
  readonly updatedAt: number | null;
  readonly error: ServerText<'message'> | null;
  readonly setupBattleStatus: ServerText<'detail'> | null;
  readonly setupBattleReason: ServerText<'message'> | null;
  readonly finalScreenName: ServerText<'name'> | null;
  readonly finalScreenVerified: boolean | null;
}

export interface NeedsYouVM {
  readonly items: readonly NeedItemVM[];
  readonly checked: readonly CheckedReadVM[];
  readonly count: number;
  readonly visible: readonly NeedItemVM[];
  readonly showAll: boolean;
  readonly line: 'issues_reported' | 'no_unresolved_issues_reported';
}

const RESOURCES: readonly ResourceId[] = [
  'status',
  'calibrate',
  'runs',
  'wizardTools',
  'wizardState',
  'config',
  'accounts',
];

function checkedRead(
  state: DashboardState,
  source: ResourceId,
  basis: 'latest' | 'lastAnswered' = 'latest',
): CheckedReadVM {
  const read = state.reads[source][basis];
  return {
    source,
    ageSec: read === null ? null : readAgeSec(state, read),
    outcome: read?.outcome.kind ?? 'not_read',
  };
}

function impactVM(impact: GuardImpact): GuardImpactVM {
  return {
    start: impact.start,
    collection: impact.collection,
    commonBlock: impact.commonBlock,
    setupMessage: fromServer('message', impact.setupMessage),
    collectionMessage: fromServer('message', impact.collectionMessage),
  };
}

function item(
  state: DashboardState,
  code: NeedItemVM['code'],
  source: ResourceId,
  sources: readonly ResourceId[] = [source],
  basis: 'latest' | 'lastAnswered' = 'latest',
): NeedItemVM {
  return {
    code,
    chip: null,
    source,
    ageSec: checkedRead(state, source, basis).ageSec,
    sources: sources.map((resource) => checkedRead(state, resource, basis)),
    impact: null,
    handoffTarget: null,
    handoffCommand: null,
    handoffBattleId: null,
    lastReadAgeSec: checkedRead(state, source, 'lastAnswered').ageSec,
    setupStep: null,
    imageCount: null,
    operationId: null,
    updatedAt: null,
    error: null,
    setupBattleStatus: null,
    setupBattleReason: null,
    finalScreenName: null,
    finalScreenVerified: null,
  };
}

/** Live issues use the newest read; failed reads never inherit a permitting guard. */
export function selectNeedsYou(state: DashboardState): NeedsYouVM {
  const status = latestRead(state, 'status');
  const body = status?.outcome.kind === 'answered' ? status.outcome.body : undefined;
  const observedAt = status === null ? NaN : status.arrivedAtMs / 1000;
  const impact = guardImpact(
    own(body, 'setup_battle_guard'),
    own(body, 'collection_guard'),
    observedAt,
  );
  const items: NeedItemVM[] = [];
  if (impact.start === 'off' || impact.collection === 'on_hold') {
    const target = handoffTargetVM(handoffTarget(body, observedAt), state.epoch);
    items.push({
      ...item(state, 'launch_guard', 'status'),
      impact: impactVM(impact),
      handoffTarget: target,
      handoffCommand: target === null ? null : { ...target, attestation: 'new_manual_battle' },
      handoffBattleId: fromServer('id', target?.setupBattleId),
      operationId: fromServer('id', ownPath(body, 'setup_battle_guard', 'operation_id')),
    });
  }
  const setup = setupOutcome(contextReadOf(latestRead(state, 'calibrate')), body, observedAt);
  if (setup !== null) {
    const verified = own(setup.finalScreen, 'verified');
    items.push({
      ...item(state, 'setup_outcome', 'calibrate', ['calibrate', 'status']),
      chip: chipVM(setup.decision),
      impact: impactVM(setup.impact),
      operationId: fromServer('id', setup.operationId),
      updatedAt: setup.updatedAt,
      error:
        fromServer('message', own(setup.error, 'message')) ?? fromServer('message', setup.error),
      setupBattleStatus: fromServer('detail', own(setup.setupBattle, 'status')),
      setupBattleReason: fromServer('message', own(setup.setupBattle, 'reason')),
      finalScreenName: fromServer('name', own(setup.finalScreen, 'name')),
      finalScreenVerified: typeof verified === 'boolean' ? verified : null,
    });
  }
  const need = statusNeed(contextReadOf(status));
  if (need !== null) items.push({ ...item(state, 'status_need', 'status'), chip: chipVM(need) });
  const setupStep = finishSetupNeed(
    answeredBody(state, 'wizardTools'),
    answeredBody(state, 'accounts'),
    answeredBody(state, 'config'),
    answeredBody(state, 'runs'),
  );
  if (setupStep !== null) {
    const source: ResourceId =
      setupStep === 'tools'
        ? 'wizardTools'
        : setupStep === 'profile'
          ? 'accounts'
          : setupStep === 'connection'
            ? 'config'
            : 'runs';
    items.push({
      ...item(
        state,
        'finish_setup',
        source,
        ['wizardTools', 'accounts', 'config', 'runs'],
        'lastAnswered',
      ),
      setupStep,
    });
  }
  const runs = answeredBody(state, 'runs');
  const imageCount = otherRunsMissingImages(runs, selectRun(runs, state.selectedRun));
  if (imageCount > 0)
    items.push({
      ...item(state, 'other_runs_images', 'runs', ['runs'], 'lastAnswered'),
      imageCount,
    });
  return {
    items,
    checked: RESOURCES.map((source) => checkedRead(state, source)),
    ...needsView(items, false),
  };
}
