import {
  profilePatchFencedPatchAnswerSchema,
  profilePatchFencedSourceAnswerSchema,
} from '../contract/profilePatchFenced.ts';
import {
  profileTargetConfigAnswerSchema,
  profileTargetFilesAnswerSchema,
  profileTargetSourceAnswerSchema,
  profileTargetPatchAnswerSchema,
  profileTargetActivateAnswerSchema,
  profileTargetMoreFilesAnswerSchema,
  profileTargetMoreSourceAnswerSchema,
  profileTargetMorePatchAnswerSchema,
  profileTargetMore2SourceAnswerSchema,
  profileTargetMore2PatchAnswerSchema,
} from '../contract/profileTarget.ts';
import {
  analysisStartAnswerSchema,
  templateWriteAnswerSchema,
} from '../contract/recognitionAdvanced.ts';
import { z } from 'zod';
import { previewBackendsAnswerSchema, previewStatusAnswerSchema } from '../contract/preview.ts';
import {
  actionExecutionsAnswerSchema,
  actionExecutionsMoreAnswerSchema,
  actionsBattleDetailAnswerSchema,
  actionsMoreBattleDetailAnswerSchema,
  actionsProjectionReadAnswerSchema,
} from '../contract/actions.ts';
import { automationAccessAnswerSchema } from '../contract/automationAccess.ts';
import {
  accountsAnswerSchema,
  battleActionsAnswerSchema,
  battleDetailAnswerSchema,
  battlesActiveAnswerSchema,
  battlesHistoryAnswerSchema,
  calibrateStatusAnswerSchema,
  configAnswerSchema,
  controlAnswerSchema,
  controlUnknownOutcomeAnswerSchema,
  diagnosticsClientAnswerSchema,
  handoffAnswerSchema,
  readinessAnswerSchema,
  reconnectAnswerSchema,
  runControlsAnswerSchema,
  runsAnswerSchema,
  scheduleStatusAnswerSchema,
  statusAnswerSchema,
  wizardEmulatorsAnswerSchema,
  wizardStateAnswerSchema,
  wizardToolsAnswerSchema,
  writeUnknownOutcomeAnswerSchema,
} from '../contract/dashboard.ts';
import { postHarvestAnswerSchema } from '../contract/postHarvest.ts';
import {
  profilesAccountsPostAnswerSchema,
  profilesAccountsReadAnswerSchema,
  profilesActivatePostAnswerSchema,
  profilesConfigReadAnswerSchema,
  profilesCopyPostAnswerSchema,
  profilesFilesAfterAccountAnswerSchema,
  profilesFilesAfterWriteAnswerSchema,
  profilesFilesReadAnswerSchema,
  profilesPromotePostAnswerSchema,
  profilesSourceAfterPromoteAnswerSchema,
  profilesSourceAfterTierAnswerSchema,
  profilesSourceReadAnswerSchema,
  profilesTierPostAnswerSchema,
} from '../contract/profiles.ts';
import {
  liveControlsAnswerSchema,
  liveControlsReadAnswerSchema,
  runControlsSaveAnswerSchema,
  runControlsSaveReadAnswerSchema,
} from '../contract/runControls.ts';
import { refusalAnswerSchema } from '../contract/refusal.ts';
import { startTransferAnswerSchema } from '../contract/startTransfer.ts';
import { harvestStatusSchema } from '../contract/status.ts';
import { schedulePatchAnswerSchema } from '../contract/schedule.ts';
import {
  collectionPatchAnswerSchema,
  collectionSourceAnswerSchema,
  settingsPostAnswerSchema,
  settingsReadAnswerSchema,
  vocabReadAnswerSchema,
} from '../contract/collection.ts';
import {
  emulatorsAdoptAnswerSchema,
  emulatorsConfigAnswerSchema,
  emulatorsInstallAnswerSchema,
  emulatorsInventoryAnswerSchema,
  emulatorsLaunchAnswerSchema,
  emulatorsPrepareAnswerSchema,
  emulatorsResolutionAnswerSchema,
  emulatorsToolsAnswerSchema,
  emulatorsWizardStateAnswerSchema,
} from '../contract/emulators.ts';
import {
  calibrateApplyAnswerSchema,
  calibrateObserveAnswerSchema,
  calibrateStartAnswerSchema,
  calibrateStopAnswerSchema,
  recognitionConfigAnswerSchema,
  recognitionStatusAnswerSchema,
  recognitionStatusReadbackAnswerSchema,
} from '../contract/recognition.ts';
import {
  blueStacksInventoryAnswerSchema,
  blueStacksPrepareAnswerSchema,
  battleDetailsReadAnswerSchema,
  fenceCalibrateStatusAnswerSchema,
  fenceConfigAnswerSchema,
  fencedAddAnswerSchema,
  fencedAddSourceAnswerSchema,
  fencedAddConfigAnswerSchema,
  fencedAnalysisStartAnswerSchema,
  fencedApplyAnswerSchema,
  fencedObserveAnswerSchema,
  fencedStartAnswerSchema,
  fencedStopAnswerSchema,
  firstPolicyPatchAnswerSchema,
  firstPolicySourceAnswerSchema,
  planDeleteAnswerSchema,
  planDeleteSourceAnswerSchema,
  resetAllAnswerSchema,
  resetAllReadAnswerSchema,
  mixedResetAnswerSchema,
  mixedResetReadAnswerSchema,
  codedAddAnswerSchema,
  codedAddSourceAnswerSchema,
  queueBoundsSaveAnswerSchema,
  queueBoundsReadAnswerSchema,
  queueBoundsLiveAnswerSchema,
  queueBoundsBattleReadAnswerSchema,
  runEntryPatchAnswerSchema,
  runEntrySourceAnswerSchema,
  runTemplatesAddAnswerSchema,
  runTemplatesAddSourceAnswerSchema,
  runTemplatesAnswerSchema,
  runTemplatesListAnswerSchema,
} from '../contract/r12Families.ts';
import {
  shoppingBattleReadAnswerSchema,
  shoppingBattleWriteAnswerSchema,
  shoppingLibraryReadAnswerSchema,
  shoppingPolicyPatchAnswerSchema,
  shoppingRunReadAnswerSchema,
  shoppingRunSaveAnswerSchema,
} from '../contract/shopping.ts';
import {
  configFieldsConfigAnswerSchema,
  configFieldsMoreConfigAnswerSchema,
  configFieldsMoreSettingsReadAnswerSchema,
  configFieldsMoreSettingsSaveAnswerSchema,
  configFieldsMore2ConfigAnswerSchema,
  configFieldsMore2SettingsReadAnswerSchema,
  configFieldsMore2SettingsSaveAnswerSchema,
  configFieldsMore2SourceAnswerSchema,
  configFieldsSettingsReadAnswerSchema,
  configFieldsSettingsSaveAnswerSchema,
  restoreConfigAnswerSchema,
  restoreEligibilityConfigAnswerSchema,
  switchConfigAnswerSchema,
  switchSettingsReadAnswerSchema,
  switchSettingsSaveAnswerSchema,
} from '../contract/configFields.ts';
import {
  stopCancelAnswerSchema,
  stopConflictAnswerSchema,
  stopKillAnswerSchema,
} from '../contract/stop.ts';

/**
 * The API boundary (spec 7.1, two schema levels per endpoint). Every answer the
 * app receives is parsed here, against the schemas generated from the backend
 * contract fixtures, before anything else sees it:
 *
 *  - strict: the generated strict schema of the endpoint's fixture families
 *    (unknown fields refused). Its verdict is kept on the answer as `exact` or
 *    `drift`, with the first issues. The strict level describes the recorded
 *    samples exactly - fixed instance names, `null` where the fixture had no
 *    account - so ordinary production values fail it; it detects drift, it is
 *    not what the page acts on (tests/unit/api/wire.test.ts shows both).
 *  - wire (runtime): derived mechanically from the SAME generated schemas: the
 *    envelope must be a JSON object, and each top-level member is kept only
 *    when its JSON type is one the contract records for that member name. A
 *    member the contract does not know, or of a type it does not record, is
 *    dropped, so core reads it as absent - Unknown, never a guess - and one
 *    malformed member makes only that member unknown. Nested values stay raw
 *    for core to judge field by field with its own-property readers (S-095).
 *
 * Parsed bodies are deep-frozen: server data is immutable once received.
 */

export type JsonType = 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null';

const ALL_TYPES: readonly JsonType[] = ['object', 'array', 'string', 'number', 'boolean', 'null'];

/** The JSON type of a parsed JSON value (null for anything JSON cannot hold). */
export function jsonTypeOf(value: unknown): JsonType | null {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  switch (typeof value) {
    case 'object':
      return 'object';
    case 'string':
      return 'string';
    case 'number':
      return 'number';
    case 'boolean':
      return 'boolean';
    default:
      return null;
  }
}

function literalTypes(values: Iterable<unknown>): readonly JsonType[] {
  const out: JsonType[] = [];
  for (const value of values) {
    const type = jsonTypeOf(value);
    if (type !== null) out.push(type);
  }
  return out;
}

/** The JSON types a generated schema accepts at its own top level. Fails loudly on a kind it does not know. */
export function typesOf(schema: unknown): readonly JsonType[] {
  if (schema instanceof z.ZodObject || schema instanceof z.ZodRecord) return ['object'];
  if (schema instanceof z.ZodArray) return ['array'];
  if (schema instanceof z.ZodString) return ['string'];
  if (schema instanceof z.ZodNumber) return ['number'];
  if (schema instanceof z.ZodBoolean) return ['boolean'];
  if (schema instanceof z.ZodNull) return ['null'];
  if (schema instanceof z.ZodEnum) return literalTypes(schema.options);
  if (schema instanceof z.ZodLiteral) return literalTypes(schema.values);
  if (schema instanceof z.ZodNullable) return [...typesOf(schema.unwrap()), 'null'];
  if (schema instanceof z.ZodOptional) return typesOf(schema.unwrap());
  if (schema instanceof z.ZodUnion) return schema.options.flatMap((option) => typesOf(option));
  if (schema instanceof z.ZodUnknown || schema instanceof z.ZodAny) return ALL_TYPES;
  throw new Error('api/wire: a contract schema of a kind the wire level does not know');
}

/** The object variants of a schema (a union is unwrapped; anything else has none). */
function objectShapes(schema: unknown): readonly Readonly<Record<string, unknown>>[] {
  if (schema instanceof z.ZodObject) return [schema.shape];
  if (schema instanceof z.ZodUnion) return schema.options.flatMap((option) => objectShapes(option));
  if (schema instanceof z.ZodOptional || schema instanceof z.ZodNullable) {
    return objectShapes(schema.unwrap());
  }
  return [];
}

/** Every body member name the answer schemas record, with the JSON types recorded for it. */
export function bodyMembers(
  answers: readonly z.ZodType[],
): ReadonlyMap<string, ReadonlySet<JsonType>> {
  const members = new Map<string, Set<JsonType>>();
  for (const answer of answers) {
    for (const envelope of objectShapes(answer)) {
      for (const body of objectShapes(envelope['body'])) {
        for (const [name, member] of Object.entries(body)) {
          const types = members.get(name) ?? new Set<JsonType>();
          for (const type of typesOf(member)) types.add(type);
          members.set(name, types);
        }
      }
    }
  }
  return members;
}

/** The generated schemas the endpoints are built from: the default contract, or another fixture's in tests. */
export interface ContractSchemas {
  readonly harvestStatus: z.ZodType;
  readonly postHarvest: z.ZodType;
  readonly automationAccess: z.ZodType;
  readonly refusal: z.ZodType;
  readonly startTransfer: z.ZodType;
  readonly stopCancel: z.ZodType;
  /** The Stop conflicts (`stop_operation.conflicts`, 409): changed operation, partial outcome. */
  readonly stopConflict: z.ZodType;
  readonly stopKill: z.ZodType;
  readonly statusBody: z.ZodObject;
  readonly config: z.ZodType;
  readonly accounts: z.ZodType;
  readonly runs: z.ZodType;
  readonly calibrate: z.ZodType;
  readonly battlesActive: z.ZodType;
  readonly battlesHistory: z.ZodType;
  readonly battleDetail: z.ZodType;
  readonly schedule: z.ZodType;
  readonly wizardTools: z.ZodType;
  readonly wizardState: z.ZodType;
  readonly wizardEmulators: z.ZodType;
  readonly runControls: z.ZodType;
  readonly readiness: z.ZodType;
  readonly control: z.ZodType;
  readonly controlUnknown: z.ZodType;
  readonly writeUnknown: z.ZodType;
  readonly handoff: z.ZodType;
  readonly reconnect: z.ZodType;
  readonly battleActions: z.ZodType;
  readonly diagnostics: z.ZodType;
  /** POST /api/run-controls (pages fixture `run_controls_save`, its POST cases). */
  readonly runControlsSave: z.ZodType;
  /** GET /api/run-controls as the pages fixture records it to settle a save. */
  readonly runControlsSaveRead: z.ZodType;
  /** POST /api/operations/{id}/live-controls (pages fixture `run_controls_live`, its POST cases). */
  readonly liveControls: z.ZodType;
  /** GET /api/battles/active as the pages fixture records it to settle a live change. */
  readonly liveControlsRead: z.ZodType;
}

/** The production contract: the checkout fixtures' generated modules (r6 harvest, B-04). */
export const DEFAULT_CONTRACT: ContractSchemas = {
  harvestStatus: harvestStatusSchema,
  postHarvest: postHarvestAnswerSchema,
  automationAccess: automationAccessAnswerSchema,
  refusal: refusalAnswerSchema,
  startTransfer: startTransferAnswerSchema,
  stopCancel: stopCancelAnswerSchema,
  stopConflict: stopConflictAnswerSchema,
  stopKill: stopKillAnswerSchema,
  statusBody: statusAnswerSchema.shape.body,
  config: configAnswerSchema,
  accounts: accountsAnswerSchema,
  runs: runsAnswerSchema,
  calibrate: calibrateStatusAnswerSchema,
  battlesActive: battlesActiveAnswerSchema,
  battlesHistory: battlesHistoryAnswerSchema,
  battleDetail: battleDetailAnswerSchema,
  schedule: scheduleStatusAnswerSchema,
  wizardTools: wizardToolsAnswerSchema,
  wizardState: wizardStateAnswerSchema,
  wizardEmulators: wizardEmulatorsAnswerSchema,
  runControls: runControlsAnswerSchema,
  readiness: readinessAnswerSchema,
  control: controlAnswerSchema,
  controlUnknown: controlUnknownOutcomeAnswerSchema,
  writeUnknown: writeUnknownOutcomeAnswerSchema,
  handoff: handoffAnswerSchema,
  reconnect: reconnectAnswerSchema,
  battleActions: battleActionsAnswerSchema,
  diagnostics: diagnosticsClientAnswerSchema,
  runControlsSave: runControlsSaveAnswerSchema,
  runControlsSaveRead: runControlsSaveReadAnswerSchema,
  liveControls: liveControlsAnswerSchema,
  liveControlsRead: liveControlsReadAnswerSchema,
};

/** Every Dashboard read of spec section 7 (7.2, plus the click-time reads of INV-1 and Start). */
export type ReadEndpointId =
  | 'previewBackends'
  | 'previewStatus'
  | 'status'
  | 'config'
  | 'accounts'
  | 'runs'
  | 'calibrate'
  | 'battlesActive'
  | 'battles'
  | 'battleDetail'
  | 'actionExecutions'
  | 'schedule'
  | 'wizardTools'
  | 'wizardState'
  | 'wizardEmulators'
  | 'runControls'
  | 'readiness'
  | 'harvest'
  | 'profileFiles'
  | 'profileSource'
  | 'settings'
  | 'vocab'
  | 'runTemplates'
  | 'resolution';

/** Every Dashboard write of spec 7.4 (diagnostics included, C-20). */
export type WriteEndpointId =
  | 'harvestWrite'
  | 'automationAccess'
  | 'cancel'
  | 'control'
  | 'handoff'
  | 'reconnect'
  | 'actions'
  | 'runControlsSave'
  | 'profilePatch'
  | 'runTemplatesAdd'
  | 'liveControls'
  | 'accountsWrite'
  | 'accountTier'
  | 'profileActivate'
  | 'profileCopy'
  | 'profilePromote'
  | 'settingsSave'
  | 'adopt'
  | 'launch'
  | 'prepare'
  | 'toolsInstall'
  | 'calibrateStart'
  | 'calibrateObserve'
  | 'calibrateApply'
  | 'calibrateStop'
  | 'analysisStart'
  | 'templateWrite'
  | 'diagnostics';

export type EndpointId = ReadEndpointId | WriteEndpointId;

/** Every write endpoint, once (the persistence of write records reads its endpoints from here). */
export const WRITE_ENDPOINT_IDS = [
  'harvestWrite',
  'automationAccess',
  'cancel',
  'control',
  'handoff',
  'reconnect',
  'actions',
  'runControlsSave',
  'profilePatch',
  'runTemplatesAdd',
  'liveControls',
  'accountsWrite',
  'accountTier',
  'profileActivate',
  'profileCopy',
  'profilePromote',
  'settingsSave',
  'adopt',
  'launch',
  'prepare',
  'toolsInstall',
  'calibrateStart',
  'calibrateObserve',
  'calibrateApply',
  'calibrateStop',
  'analysisStart',
  'templateWrite',
  'diagnostics',
] as const satisfies readonly WriteEndpointId[];

/** Compile-time proof the list names every WriteEndpointId (a missing one makes this `false`). */
export const WRITE_ENDPOINT_IDS_COMPLETE: [
  Exclude<WriteEndpointId, (typeof WRITE_ENDPOINT_IDS)[number]>,
] extends [never]
  ? true
  : false = true;

export interface Endpoint {
  readonly id: EndpointId;
  readonly method: 'GET' | 'POST';
  /** The strict answer schemas (`{status, body}`) of the endpoint's fixture families. */
  readonly strict: readonly z.ZodType[];
  /** The wire level: body member name to the JSON types the contract records for it. */
  readonly members: ReadonlyMap<string, ReadonlySet<JsonType>>;
}

function endpoint(id: EndpointId, method: 'GET' | 'POST', strict: readonly z.ZodType[]): Endpoint {
  return Object.freeze({ id, method, strict, members: bodyMembers(strict) });
}

/** The endpoint table built from `contract`. */
export function createEndpoints(contract: ContractSchemas): Readonly<Record<EndpointId, Endpoint>> {
  // GET /api/status: every root member the Dashboard fixture records, plus `harvest`
  // (its own fixture, harvest_status_v1.json) and `runtime` (the machine's build and port
  // identity, outside the Dashboard contract by the fixture's own note).
  const status = z.strictObject({
    status: z.literal(200),
    body: contract.statusBody.extend({
      harvest: contract.harvestStatus,
      runtime: z.unknown().optional(),
    }),
  });
  // GET /api/harvest (INV-1 click-time read): `jsonify(ok=True, harvest=...)` in dashboard.py.
  const harvest = z.strictObject({
    status: z.literal(200),
    body: z.strictObject({ ok: z.literal(true), harvest: contract.harvestStatus }),
  });
  return Object.freeze({
    previewBackends: endpoint('previewBackends', 'GET', [previewBackendsAnswerSchema]),
    previewStatus: endpoint('previewStatus', 'GET', [previewStatusAnswerSchema]),
    status: endpoint('status', 'GET', [status]),
    config: endpoint('config', 'GET', [
      // F6-15: the config after an active-instance switch and after a loadout restore save.
      configFieldsConfigAnswerSchema,
      configFieldsMoreConfigAnswerSchema,
      configFieldsMore2ConfigAnswerSchema,
      switchConfigAnswerSchema,
      restoreConfigAnswerSchema,
      restoreEligibilityConfigAnswerSchema,
      profileTargetConfigAnswerSchema,
      fencedAddConfigAnswerSchema,
      contract.config,
      profilesConfigReadAnswerSchema,
      emulatorsConfigAnswerSchema,
      recognitionConfigAnswerSchema,
      fenceConfigAnswerSchema,
    ]),
    accounts: endpoint('accounts', 'GET', [contract.accounts, profilesAccountsReadAnswerSchema]),
    // Profiles page (profiles brief P1 to P5): the pages fixture's profile file list and the
    // source of one profile, as the reads and the writes' settling reads record them.
    profileFiles: endpoint('profileFiles', 'GET', [
      profileTargetFilesAnswerSchema,
      profileTargetMoreFilesAnswerSchema,
      profilesFilesReadAnswerSchema,
      profilesFilesAfterAccountAnswerSchema,
      profilesFilesAfterWriteAnswerSchema,
    ]),
    profileSource: endpoint('profileSource', 'GET', [
      profilePatchFencedSourceAnswerSchema,
      profileTargetSourceAnswerSchema,
      profileTargetMoreSourceAnswerSchema,
      profileTargetMore2SourceAnswerSchema,
      configFieldsMore2SourceAnswerSchema,
      profilesSourceReadAnswerSchema,
      profilesSourceAfterTierAnswerSchema,
      profilesSourceAfterPromoteAnswerSchema,
      collectionSourceAnswerSchema,
      firstPolicySourceAnswerSchema,
      // F6-11 RL3, RL4, RL8 (r12 candidate): the profile reads around each write.
      runEntrySourceAnswerSchema,
      // F6-12: the shared shopping library before and after its edits.
      shoppingLibraryReadAnswerSchema,
      runTemplatesAddSourceAnswerSchema,
      fencedAddSourceAnswerSchema,
      codedAddSourceAnswerSchema,
      planDeleteSourceAnswerSchema,
    ]),
    runs: endpoint('runs', 'GET', [contract.runs]),
    // C5, F6C-R1-04: the run templates carry the shipped default gathering policies.
    runTemplatesAdd: endpoint('runTemplatesAdd', 'POST', [
      runTemplatesAddAnswerSchema,
      fencedAddAnswerSchema,
      codedAddAnswerSchema,
    ]),
    runTemplates: endpoint('runTemplates', 'GET', [
      runTemplatesAnswerSchema,
      runTemplatesListAnswerSchema,
    ]),
    calibrate: endpoint('calibrate', 'GET', [
      contract.calibrate,
      recognitionStatusAnswerSchema,
      recognitionStatusReadbackAnswerSchema,
      fenceCalibrateStatusAnswerSchema,
    ]),
    battlesActive: endpoint('battlesActive', 'GET', [
      contract.battlesActive,
      contract.liveControlsRead,
      // F6-11 RL2 and RL7 (r12 candidate): reads around a reset of every override, and a
      // battle with a seen Ad gems offer, a failed attempt and a Floating gems refusal.
      resetAllReadAnswerSchema,
      mixedResetReadAnswerSchema,
      queueBoundsBattleReadAnswerSchema,
      battleDetailsReadAnswerSchema,
      // F6-12: a battle's shopping queue changes and Try once commands, requested to completed.
      shoppingBattleReadAnswerSchema,
      // A2-7 R0: the battle read carrying the timed-action projection runtime.actions.
      actionsProjectionReadAnswerSchema,
    ]),
    battles: endpoint('battles', 'GET', [contract.battlesHistory]),
    // A2-7 R0: a battle detail whose executions block lists recorded action executions.
    battleDetail: endpoint('battleDetail', 'GET', [
      contract.battleDetail,
      actionsBattleDetailAnswerSchema,
      actionsMoreBattleDetailAnswerSchema,
    ]),
    // A2-7 AC4: GET /api/actions/executions, the generic route behind History's older executions.
    actionExecutions: endpoint('actionExecutions', 'GET', [
      actionExecutionsAnswerSchema,
      actionExecutionsMoreAnswerSchema,
    ]),
    schedule: endpoint('schedule', 'GET', [contract.schedule]),
    wizardTools: endpoint('wizardTools', 'GET', [contract.wizardTools, emulatorsToolsAnswerSchema]),
    wizardState: endpoint('wizardState', 'GET', [
      contract.wizardState,
      emulatorsWizardStateAnswerSchema,
    ]),
    wizardEmulators: endpoint('wizardEmulators', 'GET', [
      contract.wizardEmulators,
      emulatorsInventoryAnswerSchema,
      blueStacksInventoryAnswerSchema,
    ]),
    // Emulators page (M1 to M6): the explicit resolution check and the guarded setup writes.
    resolution: endpoint('resolution', 'GET', [emulatorsResolutionAnswerSchema]),
    adopt: endpoint('adopt', 'POST', [emulatorsAdoptAnswerSchema]),
    launch: endpoint('launch', 'POST', [emulatorsLaunchAnswerSchema]),
    prepare: endpoint('prepare', 'POST', [
      emulatorsPrepareAnswerSchema,
      blueStacksPrepareAnswerSchema,
    ]),
    toolsInstall: endpoint('toolsInstall', 'POST', [emulatorsInstallAnswerSchema]),
    // Recognition page (screens brief R1 to R8): the calibrator's launches, apply and stop.
    // r12 candidate recognition_context_fence: apply takes expected_context (409
    // context_changed + current_context, 400 invalid_request); start, observe and analysis
    // start are refused by the operation's frozen context; Stop is never refused for one.
    calibrateStart: endpoint('calibrateStart', 'POST', [
      calibrateStartAnswerSchema,
      fencedStartAnswerSchema,
    ]),
    calibrateObserve: endpoint('calibrateObserve', 'POST', [
      calibrateObserveAnswerSchema,
      fencedObserveAnswerSchema,
    ]),
    calibrateApply: endpoint('calibrateApply', 'POST', [
      calibrateApplyAnswerSchema,
      fencedApplyAnswerSchema,
    ]),
    calibrateStop: endpoint('calibrateStop', 'POST', [
      calibrateStopAnswerSchema,
      fencedStopAnswerSchema,
    ]),
    // R9, R11: their strict schemas are the advanced repairs' own (api/recognitionAdvanced.ts).
    analysisStart: endpoint('analysisStart', 'POST', [
      analysisStartAnswerSchema,
      fencedAnalysisStartAnswerSchema,
    ]),
    templateWrite: endpoint('templateWrite', 'POST', [templateWriteAnswerSchema]),
    runControls: endpoint('runControls', 'GET', [
      queueBoundsReadAnswerSchema,
      contract.runControls,
      contract.runControlsSaveRead,
      // F6-12: saved queues, legacy conversion previews and the reads around queue saves.
      shoppingRunReadAnswerSchema,
    ]),
    readiness: endpoint('readiness', 'GET', [contract.readiness]),
    harvest: endpoint('harvest', 'GET', [harvest]),
    harvestWrite: endpoint('harvestWrite', 'POST', [contract.postHarvest, contract.refusal]),
    automationAccess: endpoint('automationAccess', 'POST', [
      contract.automationAccess,
      contract.refusal,
      contract.writeUnknown,
    ]),
    cancel: endpoint('cancel', 'POST', [
      contract.stopCancel,
      contract.stopConflict,
      contract.refusal,
      contract.writeUnknown,
    ]),
    control: endpoint('control', 'POST', [
      contract.control,
      contract.controlUnknown,
      contract.refusal,
      contract.startTransfer,
      contract.stopKill,
    ]),
    handoff: endpoint('handoff', 'POST', [contract.handoff, contract.writeUnknown]),
    reconnect: endpoint('reconnect', 'POST', [contract.reconnect]),
    actions: endpoint('actions', 'POST', [contract.battleActions, shoppingBattleWriteAnswerSchema]),
    // POST /api/run-controls (Runs page, RUN-05): the read's shape with the new revision,
    // {ok:false, error[, code]} with 400/409, or a text 500 (pages fixture run_controls_save).
    runControlsSave: endpoint('runControlsSave', 'POST', [
      queueBoundsSaveAnswerSchema,
      contract.runControlsSave,
      shoppingRunSaveAnswerSchema,
    ]),
    profilePatch: endpoint('profilePatch', 'POST', [
      profilePatchFencedPatchAnswerSchema,
      profileTargetPatchAnswerSchema,
      profileTargetMorePatchAnswerSchema,
      profileTargetMore2PatchAnswerSchema,
      schedulePatchAnswerSchema,
      collectionPatchAnswerSchema,
      firstPolicyPatchAnswerSchema,
      // F6-11 RL3 (one entry field of a run) and RL8 (the whole plan deleted), r12 candidate.
      runEntryPatchAnswerSchema,
      planDeleteAnswerSchema,
      // F6-12: the shared shopping library (create, copy, edit, delete unused).
      shoppingPolicyPatchAnswerSchema,
    ]),
    // Collection (C1 to C4) and every typed settings module: the read and its save.
    settings: endpoint('settings', 'GET', [
      settingsReadAnswerSchema,
      // F6-15: reads after the typed module saves and the active-instance switches.
      configFieldsSettingsReadAnswerSchema,
      configFieldsMoreSettingsReadAnswerSchema,
      configFieldsMore2SettingsReadAnswerSchema,
      switchSettingsReadAnswerSchema,
    ]),
    settingsSave: endpoint('settingsSave', 'POST', [
      settingsPostAnswerSchema,
      configFieldsSettingsSaveAnswerSchema,
      configFieldsMoreSettingsSaveAnswerSchema,
      configFieldsMore2SettingsSaveAnswerSchema,
      switchSettingsSaveAnswerSchema,
    ]),
    vocab: endpoint('vocab', 'GET', [vocabReadAnswerSchema]),
    // POST /api/operations/{id}/live-controls (RUN-08): pages fixture run_controls_live; the
    // Dashboard fixture's battle_actions records the same route's accepted change.
    liveControls: endpoint('liveControls', 'POST', [
      contract.liveControls,
      contract.battleActions,
      // F6-11 RL2 (r12 candidate): several paths reset in one request.
      resetAllAnswerSchema,
      mixedResetAnswerSchema,
      queueBoundsLiveAnswerSchema,
      // F6-12: a whole shopping queue sent as one path.
      shoppingBattleWriteAnswerSchema,
    ]),
    // POST /api/accounts (select or create), /api/account-tier and the profile file writes.
    accountsWrite: endpoint('accountsWrite', 'POST', [profilesAccountsPostAnswerSchema]),
    accountTier: endpoint('accountTier', 'POST', [profilesTierPostAnswerSchema]),
    profileActivate: endpoint('profileActivate', 'POST', [
      profilesActivatePostAnswerSchema,
      profileTargetActivateAnswerSchema,
    ]),
    profileCopy: endpoint('profileCopy', 'POST', [profilesCopyPostAnswerSchema]),
    profilePromote: endpoint('profilePromote', 'POST', [profilesPromotePostAnswerSchema]),
    diagnostics: endpoint('diagnostics', 'POST', [contract.diagnostics]),
  });
}

export const ENDPOINTS: Readonly<Record<EndpointId, Endpoint>> = createEndpoints(DEFAULT_CONTRACT);

/** One answer as the app keeps it: the HTTP status and the wire-level body. */
export interface WireAnswer {
  readonly status: number;
  /** A frozen plain object holding only the members the wire level kept, or undefined. */
  readonly body: unknown;
  /** The strict verdict: the answer is a recorded contract shape, or it drifted from it. */
  readonly contract: 'exact' | 'drift';
  /** Why: the first strict issues, then every member the wire level dropped (`dropped:<name>`). */
  readonly issues: readonly string[];
  /** False when the body was not JSON (it is then kept as `{text}`, as the fixtures record a non-JSON page). */
  readonly json: boolean;
}

function deepFreeze(value: unknown): unknown {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    for (const member of Object.values(value)) deepFreeze(member);
    Object.freeze(value);
  }
  return value;
}

/** `value` when it is a plain object as JSON makes it, else undefined. */
function plainObject(value: unknown): object | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null ? value : undefined;
}

/** The wire level of a body: only the members whose JSON type the contract records for that name. */
export function projectBody(
  body: unknown,
  members: ReadonlyMap<string, ReadonlySet<JsonType>>,
): { readonly body: unknown; readonly dropped: readonly string[] } {
  const object = plainObject(body);
  if (object === undefined) {
    return { body: undefined, dropped: ['body_not_object'] };
  }
  const kept: [string, unknown][] = [];
  const dropped: string[] = [];
  for (const [name, value] of Object.entries(object)) {
    const type = jsonTypeOf(value);
    if (type !== null && members.get(name)?.has(type) === true) {
      kept.push([name, value]);
    } else {
      dropped.push(name);
    }
  }
  return { body: deepFreeze(Object.fromEntries(kept)), dropped };
}

function strictVerdict(
  endpointValue: Endpoint,
  answer: unknown,
): { readonly contract: 'exact' | 'drift'; readonly issues: readonly string[] } {
  let issues: readonly string[] = [];
  for (const schema of endpointValue.strict) {
    const parsed = schema.safeParse(answer);
    if (parsed.success) return { contract: 'exact', issues: [] };
    if (issues.length === 0) {
      issues = parsed.error.issues
        .slice(0, 5)
        .map((issue) => `${issue.path.map(String).join('.')}: ${issue.code}`);
    }
  }
  return { contract: 'drift', issues };
}

/** Parse one HTTP answer of `endpointValue` at the boundary. Never throws. */
export function parseAnswer(endpointValue: Endpoint, status: number, text: string): WireAnswer {
  let raw: unknown;
  let json = true;
  try {
    raw = JSON.parse(text);
  } catch {
    json = false;
    raw = { text };
  }
  const verdict = strictVerdict(endpointValue, { status, body: raw });
  const projected = projectBody(raw, endpointValue.members);
  return Object.freeze({
    status,
    body: projected.body,
    contract: verdict.contract,
    issues: Object.freeze([
      ...verdict.issues,
      ...projected.dropped.map((name) => `dropped:${name}`),
    ]),
    json,
  });
}
