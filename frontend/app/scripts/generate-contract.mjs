// @ts-check
/** Strict fixture schemas only. R8 owns the deliberately tolerant runtime wire boundary. */
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { format, resolveConfig } from 'prettier';
import { createHash } from 'node:crypto';
import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  FIXTURE_PATH,
  familyHashes,
  loadJsonWithIdentity,
} from './contract-fixture.mjs';

const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const GENERATED_DIR = path.join(APP, 'src/contract/generated');
export { DASHBOARD_FIXTURE_PATH };

/**
 * These are protocol vocabularies, not identifiers, messages, timestamps, labels,
 * or revisions. Numeric HTTP status is handled separately as a literal union.
 * Every listed key occurs as a string in the harvest fixtures (including role in
 * r6). A dashboard descriptor uses the same rule, at its own raw key paths.
 */
export const DISCRIMINATOR_KEYS = new Set([
  'state',
  'status',
  'code',
  'kind',
  'choice',
  'reason',
  'basis',
  'observation_status',
  'reconciler',
  'action',
  'input',
  'operation_kind',
  'origin',
  'cadence_source',
  'changed_by',
  'role',
]);

/**
 * @typedef {{ key: string, name: string, rawPath: string, mode?: 'rows', optional?: boolean, pick?: readonly string[], source?: string, method?: 'GET' | 'POST', pathPrefix?: string }} Family
 * @typedef {{ tag: string, key: string, rawPath: RegExp, source: string }} Vocabulary
 * @typedef {{ name: string, families: readonly Family[], vocabularies?: readonly Vocabulary[], boundTo?: 'families', vocabularyFrom?: readonly { file: string, descriptor: Descriptor }[] }} Descriptor
 * @typedef {{ path: string, sha256: string, families?: Readonly<Record<string, string>> }} Identity
 * @typedef {{ name: string, rawPath: string, variant?: string, expression: string }} Declaration
 * @typedef {{ objects: Record<string, unknown>[], label: string, description: string, tag: { key: string, value: string } | null }} Group
 * @typedef {{ declarations: Declaration[], vocabularies: { vocabulary: Vocabulary, byTag: Map<string, Set<string>> }[] }} Context
 */

/**
 * One descriptor per fixture. Add the dashboard descriptor here when its backend
 * fixture exists: its own DASHBOARD_FIXTURE_PATH, families and generated module;
 * generateModule and the strict-parse test loop are fixture-independent.
 * Repeat samples are sequence objects: most steps are answers, but r6's
 * after_release is an operation readback ({error, status, stop}), not an answer.
 * @type {Descriptor}
 */
export const HARVEST_DESCRIPTOR = {
  name: 'harvest_status_v1',
  families: [
    { key: 'status', name: 'HarvestStatus', rawPath: 'harvest' },
    { key: 'post_harvest', name: 'PostHarvestAnswer', rawPath: 'answer' },
    { key: 'automation_access', name: 'AutomationAccessAnswer', rawPath: 'answer' },
    { key: 'refusals', name: 'RefusalAnswer', rawPath: 'answer' },
    { key: 'start_transfer', name: 'StartTransferAnswer', rawPath: 'answer' },
    { key: 'stop_operation.cancel', name: 'StopCancelAnswer', rawPath: 'answer' },
    {
      key: 'stop_operation.conflicts',
      name: 'StopConflictAnswer',
      rawPath: 'answer',
      optional: true,
    },
    { key: 'stop_operation.kill', name: 'StopKillAnswer', rawPath: 'answer' },
    { key: 'stop_operation.processes', name: 'StopProcess', rawPath: 'processes[]', mode: 'rows' },
    { key: 'stop_operation.repeat', name: 'StopRepeatSequence', rawPath: 'sequence' },
  ],
};

/** F6-13: binary metadata is recorded, never shipped pixels. @type {Descriptor} */
export const PREVIEW_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_preview',
  boundTo: 'families',
  families: [
    ['preview_status', 'PreviewStatusAnswer'],
    ['preview_backends', 'PreviewBackendsAnswer'],
    ['preview_stream', 'PreviewStreamAnswer'],
    ['preview_frame_png', 'PreviewFramePngAnswer'],
  ].map(([family, name]) => ({
    key: `cases.${family ?? ''}`,
    name: name ?? '',
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/**
 * The Dashboard contract fixture (spec 10.3, OD-2): every case is
 * {request: {method, path, body?}, status, body}. The strict schema of a family
 * is its answer ({status, body}) at raw response path `answer`, so nested
 * schemas are named by raw response paths (answer.body.battle.commands,
 * answer.body.battle.runtime.routines.shopping.last_outcome); the request is
 * the input that produced the answer, not part of the contract.
 * @type {Descriptor}
 */
export const DASHBOARD_DESCRIPTOR = {
  name: 'dashboard_contract_v1',
  families: [
    ['accounts', 'AccountsAnswer'],
    ['battle_actions', 'BattleActionsAnswer'],
    ['battle_detail', 'BattleDetailAnswer'],
    ['battles_active', 'BattlesActiveAnswer'],
    ['battles_history', 'BattlesHistoryAnswer'],
    ['calibrate_status', 'CalibrateStatusAnswer'],
    ['config', 'ConfigAnswer'],
    ['control', 'ControlAnswer'],
    ['control_unknown_outcomes', 'ControlUnknownOutcomeAnswer'],
    ['diagnostics_client', 'DiagnosticsClientAnswer'],
    ['handoff', 'HandoffAnswer'],
    ['readiness', 'ReadinessAnswer'],
    ['reconnect', 'ReconnectAnswer'],
    ['run_controls', 'RunControlsAnswer'],
    ['runs', 'RunsAnswer'],
    ['schedule_status', 'ScheduleStatusAnswer'],
    ['status', 'StatusAnswer'],
    ['wizard_emulators', 'WizardEmulatorsAnswer'],
    ['wizard_state', 'WizardStateAnswer'],
    ['wizard_tools', 'WizardToolsAnswer'],
    ['write_unknown_outcomes', 'WriteUnknownOutcomeAnswer'],
  ].map(([family, name]) => ({
    key: `cases.${family ?? ''}`,
    name: name ?? '',
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
  vocabularies: [
    {
      // One serializer writes every command (battle.commands[], an action's
      // command, an event's command), so a kind's states are the union of what
      // the fixture records for that kind at any of them (F-058).
      tag: 'kind',
      key: 'state',
      rawPath: /\.commands\[\]\.state$|\.command\.state$/,
      source: 'backend/towerpilot_workflows/battles.py: _command, acknowledge, _finish',
    },
  ],
};

/** The command-state vocabulary shared by every fixture that records battle commands (F-058). */
const COMMAND_STATE_VOCABULARY = DASHBOARD_DESCRIPTOR.vocabularies?.[0];
if (COMMAND_STATE_VOCABULARY === undefined) throw new Error('No command-state vocabulary');

/**
 * The Runs page's families of the pages contract fixture
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json): the saved-run save
 * (POST /api/run-controls) and the live change (POST
 * /api/operations/{id}/live-controls), each split by request method: the POST
 * answers are the write's contract, the GET cases are the reads that settle it
 * (GET /api/run-controls, GET /api/battles/active). The backend appends
 * families to this fixture, so the module is bound to the hashes of these two
 * families (`boundTo: 'families'`), not to the file: an appended family changes
 * nothing here, a changed one is drift.
 * @type {Descriptor}
 */
export const PAGES_RUNS_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_runs',
  boundTo: 'families',
  families: [
    ['run_controls_save', 'POST', 'RunControlsSaveAnswer'],
    ['run_controls_save', 'GET', 'RunControlsSaveReadAnswer'],
    ['run_controls_live', 'POST', 'LiveControlsAnswer'],
    ['run_controls_live', 'GET', 'LiveControlsReadAnswer'],
  ].map(([family, method, name]) => ({
    key: `${family ?? ''}.${method ?? ''}`,
    source: `cases.${family ?? ''}`,
    method: method === 'GET' ? 'GET' : 'POST',
    name: name ?? '',
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
  vocabularies: [COMMAND_STATE_VOCABULARY],
  // A command's states are what the backend's one serializer writes for its kind: the
  // Dashboard fixture records every one of them (battles_active), this fixture only some.
  vocabularyFrom: [{ file: DASHBOARD_FIXTURE_PATH, descriptor: DASHBOARD_DESCRIPTOR }],
};

/**
 * The Profiles page's families of the pages contract fixture (profiles brief
 * P1 to P5): the account and tier writes (`accounts_write`), the profile file
 * writes (`profile_files_write`), the profile reads (`profile_reads`) and the
 * config reads (`config_state`), each split by request method and route, since
 * every route answers its own shape. Bound to those families' hashes like the
 * Runs module: an appended family changes nothing here.
 * @type {Descriptor}
 */
export const PAGES_PROFILES_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_profiles',
  boundTo: 'families',
  families: /** @type {const} */ ([
    ['accounts_write', 'POST', '/api/accounts', 'ProfilesAccountsPostAnswer'],
    ['accounts_write', 'POST', '/api/account-tier', 'ProfilesTierPostAnswer'],
    ['accounts_write', 'GET', '/api/accounts', 'ProfilesAccountsReadAnswer'],
    ['accounts_write', 'GET', '/api/profile-files', 'ProfilesFilesAfterAccountAnswer'],
    ['accounts_write', 'GET', '/api/profile-src/', 'ProfilesSourceAfterTierAnswer'],
    ['profile_reads', 'GET', '/api/profile-files', 'ProfilesFilesReadAnswer'],
    ['profile_reads', 'GET', '/api/profile-src/', 'ProfilesSourceReadAnswer'],
    ['profile_files_write', 'POST', '/api/profile-activate', 'ProfilesActivatePostAnswer'],
    ['profile_files_write', 'POST', '/api/profile-copy', 'ProfilesCopyPostAnswer'],
    ['profile_files_write', 'POST', '/api/profile-promote', 'ProfilesPromotePostAnswer'],
    ['profile_files_write', 'GET', '/api/profile-files', 'ProfilesFilesAfterWriteAnswer'],
    ['profile_files_write', 'GET', '/api/profile-src/', 'ProfilesSourceAfterPromoteAnswer'],
    ['config_state', 'GET', '/api/config', 'ProfilesConfigReadAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/**
 * The Collection page (collection brief C1 to C6) and the shared settings-module and
 * policy editors: typed settings (`/api/settings`), the gathering policies and chores
 * in the profile source and their patches, and the vocabulary (chore names).
 * @type {Descriptor}
 */
export const PAGES_COLLECTION_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_collection',
  boundTo: 'families',
  families: /** @type {const} */ ([
    ['settings', 'GET', '/api/settings', 'SettingsReadAnswer'],
    ['settings', 'POST', '/api/settings', 'SettingsPostAnswer'],
    ['collection_policies', 'GET', '/api/profile-src/', 'CollectionSourceAnswer'],
    ['collection_policies', 'POST', '/api/profile-patch', 'CollectionPatchAnswer'],
    ['profile_reads', 'GET', '/api/vocab', 'VocabReadAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/**
 * The Emulators page (emulators brief M1 to M8): the guarded connection and setup writes
 * (adopt, launch, BlueStacks prepare, connection tools install), the discovery, resolution
 * and tools reads, and the reads that follow an adopt.
 * @type {Descriptor}
 */
export const PAGES_EMULATORS_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_emulators',
  boundTo: 'families',
  families: /** @type {const} */ ([
    ['emulator_actions', 'POST', '/api/wizard/adopt', 'EmulatorsAdoptAnswer'],
    ['emulator_actions', 'POST', '/api/wizard/launch', 'EmulatorsLaunchAnswer'],
    ['emulator_actions', 'POST', '/api/wizard/bluestacks/prepare', 'EmulatorsPrepareAnswer'],
    ['emulator_actions', 'GET', '/api/config', 'EmulatorsConfigAnswer'],
    ['emulator_actions', 'GET', '/api/wizard/state', 'EmulatorsWizardStateAnswer'],
    ['emulator_inventory', 'GET', '/api/wizard/emulators', 'EmulatorsInventoryAnswer'],
    ['emulator_inventory', 'GET', '/api/wizard/resolution', 'EmulatorsResolutionAnswer'],
    ['emulator_tools', 'GET', '/api/wizard/tools', 'EmulatorsToolsAnswer'],
    ['emulator_tools', 'POST', '/api/wizard/tools/install', 'EmulatorsInstallAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/**
 * The Recognition page's learning journey (screens brief R1 to R8): the calibrate status
 * reads and the calibrate writes (start, observe, apply, stop) with their readbacks.
 * @type {Descriptor}
 */
export const PAGES_RECOGNITION_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_recognition',
  boundTo: 'families',
  families: /** @type {const} */ ([
    ['recognition_reads', 'GET', '/api/calibrate/status', 'RecognitionStatusAnswer'],
    ['calibrate_writes', 'GET', '/api/calibrate/status', 'RecognitionStatusReadbackAnswer'],
    ['calibrate_writes', 'GET', '/api/config', 'RecognitionConfigAnswer'],
    ['calibrate_writes', 'POST', '/api/calibrate/start', 'CalibrateStartAnswer'],
    ['calibrate_writes', 'POST', '/api/calibrate/observe', 'CalibrateObserveAnswer'],
    ['calibrate_writes', 'POST', '/api/calibrate/apply', 'CalibrateApplyAnswer'],
    ['calibrate_writes', 'POST', '/api/calibrate/stop', 'CalibrateStopAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/** R9-R11: operation-bound analysis, coverage reads and sanctioned human crops.
 * @type {Descriptor}
 */
export const PAGES_RECOGNITION_ADVANCED_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_recognition_advanced',
  boundTo: 'families',
  families: /** @type {const} */ ([
    ['recognition_reads', 'GET', '/api/wizard/templates', 'AdvancedTemplatesAnswer'],
    ['recognition_reads', 'GET', '/api/wizard/required', 'AdvancedRequiredAnswer'],
    ['recognition_reads', 'GET', '/api/catalogue/modules', 'AdvancedCatalogueAnswer'],
    ['recognition_reads', 'GET', '/api/wizard/scan-plan', 'AdvancedPlanAnswer'],
    ['screen_analysis', 'POST', '/api/screen-analysis/start', 'AnalysisStartAnswer'],
    ['screen_analysis', 'GET', '/api/screen-analysis/status', 'AnalysisStatusAnswer'],
    ['screen_analysis', 'GET', '/api/screen-analysis/frame.png', 'AnalysisFrameAnswer'],
    ['recognition_images', 'GET', '/api/frame.png', 'CropFrameAnswer'],
    ['recognition_images', 'POST', '/api/template/', 'TemplateWriteAnswer'],
    ['recognition_images', 'GET', '/api/template/', 'TemplateImageAnswer'],
    ['recognition_images', 'GET', '/api/artwork/', 'ArtworkImageAnswer'],
    ['recognition_images', 'GET', '/api/wizard/templates', 'AdvancedTemplatesReadbackAnswer'],
    ['recognition_images', 'GET', '/api/wizard/scan-plan', 'AdvancedPlanReadbackAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};

/**
 * A2-7 timed actions (brief ui-pages/a2-7-actions-brief.md, R0): the action projection
 * `battle.runtime.actions` of GET /api/battles/active (`battle_actions_projection`) and the
 * execution history (`action_executions`, `action_executions_more`): the `executions` block of
 * GET /api/battles/<id> and the generic GET /api/actions/executions. Bound to the landed r17b
 * recordings in the checkout pages fixture (tests/unit/tooling/r17bCheckout.test.ts).
 * The projection reads carry battle commands, so their
 * states come from the backend's one serializer for each kind (F-058), as for run_controls_live.
 * Bound to these families' hashes like the Runs module.
 * @type {Descriptor}
 */
export const PAGES_ACTIONS_DESCRIPTOR = {
  name: 'dashboard_contract_pages_v1_actions',
  boundTo: 'families',
  vocabularies: [COMMAND_STATE_VOCABULARY],
  vocabularyFrom: [{ file: DASHBOARD_FIXTURE_PATH, descriptor: DASHBOARD_DESCRIPTOR }],
  families: /** @type {const} */ ([
    ['battle_actions_projection', 'GET', '/api/battles/active', 'ActionsProjectionReadAnswer'],
    ['action_executions', 'GET', '/api/battles/', 'ActionsBattleDetailAnswer'],
    ['action_executions', 'GET', '/api/actions/executions', 'ActionExecutionsAnswer'],
    ['action_executions_more', 'GET', '/api/battles/', 'ActionsMoreBattleDetailAnswer'],
    ['action_executions_more', 'GET', '/api/actions/executions', 'ActionExecutionsMoreAnswer'],
  ]).map(([family, method, route, name]) => ({
    key: `${family} ${method} ${route}`,
    source: `cases.${family}`,
    method,
    pathPrefix: route,
    name,
    rawPath: 'answer',
    pick: ['status', 'body'],
  })),
};
/**
 * The fixtures contract/ is generated from. CLI generation/checking and the
 * contract drift/strict-parse tests consume this same registry. Only the
 * harvest fixture follows TP_CONTRACT_FIXTURE (spec 10.4); the Dashboard
 * fixture is one file whatever harvest fixture is loaded.
 * @type {readonly {file: string, descriptor: Descriptor}[]}
 */
export const CONTRACT_INPUTS = [
  {
    file: path.join(APP, 'tests/fixtures/abilities-samples.json'),
    descriptor: {
      name: 'abilities_samples_v1',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/profile-src/', 'AbilitiesSampleSourceAnswer'],
        ['POST', '/api/profile-patch', 'AbilitiesSamplePatchAnswer'],
      ]).map(([method, route, name]) => ({
        key: `abilities_samples ${method} ${route}`,
        source: 'cases.abilities_samples',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_abilities',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['ability_policies', 'GET', '/api/profile-src/', 'AbilitiesSourceAnswer'],
        ['ability_policies', 'POST', '/api/profile-patch', 'AbilitiesPatchAnswer'],
        ['profile_reads', 'GET', '/api/profile-src/', 'AbilitiesProfileAnswer'],
        ['config_state', 'GET', '/api/config', 'AbilitiesConfigAnswer'],
      ]).map(([family, method, route, name]) => ({
        key: `${family} ${method} ${route}`,
        source: `cases.${family}`,
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    file: path.join(APP, 'tests/fixtures/equipment-samples.json'),
    descriptor: {
      name: 'equipment_samples_v1',
      boundTo: 'families',
      families: [
        {
          key: 'equipment_samples.config',
          source: 'cases.equipment_samples',
          method: 'GET',
          pathPrefix: '/api/config',
          name: 'EquipmentSampleConfigAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'equipment_samples.profile',
          source: 'cases.equipment_samples',
          method: 'GET',
          pathPrefix: '/api/profile-src/',
          name: 'EquipmentSampleProfileAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'equipment_samples.patch',
          source: 'cases.equipment_samples',
          method: 'POST',
          name: 'EquipmentSamplePatchAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
      ],
    },
  },
  { file: FIXTURE_PATH, descriptor: HARVEST_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PREVIEW_DESCRIPTOR },
  { file: DASHBOARD_FIXTURE_PATH, descriptor: DASHBOARD_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_RUNS_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_PROFILES_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_COLLECTION_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_EMULATORS_DESCRIPTOR },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_runtime',
      boundTo: 'families',
      families: [
        {
          key: 'cases.runtime_state',
          name: 'RuntimeStateAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'cases.runtime_diagnostics',
          name: 'RuntimeDiagnosticsAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
      ],
    },
  },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_RECOGNITION_DESCRIPTOR },
  {
    // The families r12 added to the pages fixture: the first gathering policy, BlueStacks, the
    // F6-11 Runs leftovers, the guarded run add and the Recognition apply fence.
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_r12',
      boundTo: 'families',
      // The reset-all answers and reads carry battle commands: their states are what the
      // backend's one serializer writes for each kind (F-058), as for run_controls_live.
      vocabularies: [COMMAND_STATE_VOCABULARY],
      vocabularyFrom: [{ file: DASHBOARD_FIXTURE_PATH, descriptor: DASHBOARD_DESCRIPTOR }],
      families: /** @type {const} */ ([
        ['collection_first_policy', 'GET', '/api/run-templates', 'RunTemplatesAnswer'],
        ['collection_first_policy', 'GET', '/api/profile-src/', 'FirstPolicySourceAnswer'],
        ['collection_first_policy', 'POST', '/api/profile-patch', 'FirstPolicyPatchAnswer'],
        [
          'emulator_bluestacks_inventory',
          'GET',
          '/api/wizard/emulators',
          'BlueStacksInventoryAnswer',
        ],
        [
          'emulator_bluestacks_inventory',
          'POST',
          '/api/wizard/bluestacks/prepare',
          'BlueStacksPrepareAnswer',
        ],
        ['run_controls_live_reset_all', 'POST', '/api/operations/', 'ResetAllAnswer'],
        ['run_controls_live_reset_all', 'GET', '/api/battles/active', 'ResetAllReadAnswer'],
        ['run_controls_live_reset_all_mixed', 'POST', '/api/operations/', 'MixedResetAnswer'],
        ['run_controls_live_reset_all_mixed', 'GET', '/api/battles/active', 'MixedResetReadAnswer'],
        ['run_templates_add_coded', 'POST', '/api/run-templates/add', 'CodedAddAnswer'],
        ['run_templates_add_coded', 'GET', '/api/profile-src/', 'CodedAddSourceAnswer'],
        ['shopping_queue_bounds', 'POST', '/api/run-controls', 'QueueBoundsSaveAnswer'],
        ['shopping_queue_bounds', 'GET', '/api/run-controls', 'QueueBoundsReadAnswer'],
        ['shopping_queue_bounds', 'POST', '/api/operations/', 'QueueBoundsLiveAnswer'],
        ['shopping_queue_bounds', 'GET', '/api/battles/active', 'QueueBoundsBattleReadAnswer'],
        ['battle_details_runtime', 'GET', '/api/battles/active', 'BattleDetailsReadAnswer'],
        ['run_entry_patch', 'POST', '/api/profile-patch', 'RunEntryPatchAnswer'],
        ['run_entry_patch', 'GET', '/api/profile-src/', 'RunEntrySourceAnswer'],
        ['run_templates_add', 'POST', '/api/run-templates/add', 'RunTemplatesAddAnswer'],
        ['run_templates_add', 'GET', '/api/profile-src/', 'RunTemplatesAddSourceAnswer'],
        ['run_templates_add', 'GET', '/api/run-templates', 'RunTemplatesListAnswer'],
        // Batch 2: the add with its optional expected_revision and expected_context guards.
        ['run_templates_add_fenced', 'POST', '/api/run-templates/add', 'FencedAddAnswer'],
        ['run_templates_add_fenced', 'GET', '/api/profile-src/', 'FencedAddSourceAnswer'],
        ['run_templates_add_fenced', 'GET', '/api/config', 'FencedAddConfigAnswer'],
        ['plan_section_delete', 'POST', '/api/profile-patch', 'PlanDeleteAnswer'],
        ['plan_section_delete', 'GET', '/api/profile-src/', 'PlanDeleteSourceAnswer'],
        ['recognition_context_fence', 'POST', '/api/calibrate/apply', 'FencedApplyAnswer'],
        ['recognition_context_fence', 'POST', '/api/calibrate/start', 'FencedStartAnswer'],
        ['recognition_context_fence', 'POST', '/api/calibrate/observe', 'FencedObserveAnswer'],
        ['recognition_context_fence', 'POST', '/api/calibrate/stop', 'FencedStopAnswer'],
        [
          'recognition_context_fence',
          'POST',
          '/api/screen-analysis/start',
          'FencedAnalysisStartAnswer',
        ],
        ['recognition_context_fence', 'GET', '/api/config', 'FenceConfigAnswer'],
        ['recognition_context_fence', 'GET', '/api/calibrate/status', 'FenceCalibrateStatusAnswer'],
        ['recognition_context_fence', 'GET', '/api/profile/', 'FenceProfileAnswer'],
      ]).map(([family, method, route, name]) => ({
        key: `${family} ${method} ${route}`,
        source: `cases.${family}`,
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_RECOGNITION_ADVANCED_DESCRIPTOR },
  { file: DASHBOARD_PAGES_FIXTURE_PATH, descriptor: PAGES_ACTIONS_DESCRIPTOR },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_equipment',
      boundTo: 'families',
      families: [
        {
          key: 'loadout_patch.POST',
          source: 'cases.loadout_patch',
          method: 'POST',
          name: 'EquipmentPatchAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'loadout_patch.GET',
          source: 'cases.loadout_patch',
          method: 'GET',
          name: 'EquipmentConfigAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'cases.config_state',
          name: 'EquipmentContextAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'cases.profile_reads',
          pathPrefix: '/api/profile-src/',
          name: 'EquipmentProfileAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
        {
          key: 'ability_policies.GET',
          source: 'cases.ability_policies',
          method: 'GET',
          pathPrefix: '/api/profile-src/',
          name: 'EquipmentDiscoveryAnswer',
          rawPath: 'answer',
          pick: ['status', 'body'],
        },
      ],
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_schedule',
      boundTo: 'families',
      families: [
        ['schedule_plan', 'SchedulePlanAnswer'],
        ['config_state', 'ScheduleConfigAnswer'],
        ['profile_reads', 'ScheduleProfileAnswer'],
        ['profile_patch', 'SchedulePatchAnswer'],
      ].map(([family, name]) => ({
        key: `cases.${family ?? ''}`,
        name: name ?? '',
        rawPath: 'answer',
        pick: ['status', 'body'],
        ...(family === 'profile_reads' ? { pathPrefix: '/api/profile-src/' } : {}),
      })),
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      // F6-12 shopping queue editor (brief ui-pages/f6-12-shopping-brief.md, family shopping_queue).
      name: 'dashboard_contract_pages_v1_shopping',
      boundTo: 'families',
      // Battle reads and writes carry commands: their states come from the backend's one
      // serializer for each kind (F-058), as for run_controls_live.
      vocabularies: [COMMAND_STATE_VOCABULARY],
      vocabularyFrom: [{ file: DASHBOARD_FIXTURE_PATH, descriptor: DASHBOARD_DESCRIPTOR }],
      families: /** @type {const} */ ([
        ['GET', '/api/run-controls', 'ShoppingRunReadAnswer'],
        ['POST', '/api/run-controls', 'ShoppingRunSaveAnswer'],
        // live-controls and actions answer with the same battle command envelope.
        ['POST', '/api/operations/', 'ShoppingBattleWriteAnswer'],
        ['GET', '/api/battles/active', 'ShoppingBattleReadAnswer'],
        ['POST', '/api/profile-patch', 'ShoppingPolicyPatchAnswer'],
        ['GET', '/api/profile-src/', 'ShoppingLibraryReadAnswer'],
      ]).map(([method, route, name]) => ({
        key: `shopping_queue ${method} ${route}`,
        source: 'cases.shopping_queue',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_profile_target',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/config', 'ProfileTargetConfigAnswer'],
        ['GET', '/api/profile-files', 'ProfileTargetFilesAnswer'],
        ['GET', '/api/profile-src/', 'ProfileTargetSourceAnswer'],
        ['POST', '/api/profile-patch', 'ProfileTargetPatchAnswer'],
        ['POST', '/api/profile-activate', 'ProfileTargetActivateAnswer'],
      ]).map(([method, route, name]) => ({
        key: `profile_target ${method} ${route}`,
        source: 'cases.profile_target',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_profile_patch_fenced',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['POST', '/api/profile-patch', 'ProfilePatchFencedPatchAnswer'],
        ['GET', '/api/profile-src/', 'ProfilePatchFencedSourceAnswer'],
      ]).map(([method, route, name]) => ({
        key: `profile_patch_fenced ${method} ${route}`,
        source: 'cases.profile_patch_fenced',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      // F6-15 configuration fields on their owning pages (brief ui-pages/f6-15-config-fields-brief.md):
      // the typed settings modules, the active-instance switch records and the loadout restore values.
      name: 'dashboard_contract_pages_v1_config_fields',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['settings_modules', 'GET', '/api/settings', 'ConfigFieldsSettingsReadAnswer'],
        ['settings_modules', 'POST', '/api/settings', 'ConfigFieldsSettingsSaveAnswer'],
        ['settings_modules', 'GET', '/api/config', 'ConfigFieldsConfigAnswer'],
        ['settings_switch_records', 'GET', '/api/settings', 'SwitchSettingsReadAnswer'],
        ['settings_switch_records', 'POST', '/api/settings', 'SwitchSettingsSaveAnswer'],
        ['settings_switch_records', 'GET', '/api/config', 'SwitchConfigAnswer'],
        ['loadout_restore', 'POST', '/api/loadout-patch', 'RestorePatchAnswer'],
        ['loadout_restore', 'GET', '/api/config', 'RestoreConfigAnswer'],
        [
          'loadout_restore_eligibility',
          'POST',
          '/api/loadout-patch',
          'RestoreEligibilityPatchAnswer',
        ],
        ['loadout_restore_eligibility', 'GET', '/api/config', 'RestoreEligibilityConfigAnswer'],
      ]).map(([family, method, route, name]) => ({
        key: `${family} ${method} ${route}`,
        source: `cases.${family}`,
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    // r12e (c945da8): F6-15 C-03 recordings, settings_modules_more.
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_config_fields_more',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/settings', 'ConfigFieldsMoreSettingsReadAnswer'],
        ['POST', '/api/settings', 'ConfigFieldsMoreSettingsSaveAnswer'],
        ['GET', '/api/config', 'ConfigFieldsMoreConfigAnswer'],
      ]).map(([method, route, name]) => ({
        key: `settings_modules_more ${method} ${route}`,
        source: 'cases.settings_modules_more',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    // r12f: F6-15 follow-up (restore-only save, profiles without blueprints, malformed lists).
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_config_fields_more_2',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/settings', 'ConfigFieldsMore2SettingsReadAnswer'],
        ['POST', '/api/settings', 'ConfigFieldsMore2SettingsSaveAnswer'],
        ['GET', '/api/config', 'ConfigFieldsMore2ConfigAnswer'],
        ['GET', '/api/profile-src/', 'ConfigFieldsMore2SourceAnswer'],
        ['POST', '/api/loadout-patch', 'ConfigFieldsMore2LoadoutPatchAnswer'],
      ]).map(([method, route, name]) => ({
        key: `settings_modules_more_2 ${method} ${route}`,
        source: 'cases.settings_modules_more_2',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    // r12f: the seven inactive run-entry edits of F6-14c not in profile_target_more.
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_profile_target_more_2',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/profile-src/', 'ProfileTargetMore2SourceAnswer'],
        ['POST', '/api/profile-patch', 'ProfileTargetMore2PatchAnswer'],
      ]).map(([method, route, name]) => ({
        key: `profile_target_more_2 ${method} ${route}`,
        source: 'cases.profile_target_more_2',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
  {
    // r12e (c945da8): F6-14 inactive-target paths not recorded in profile_target.
    file: DASHBOARD_PAGES_FIXTURE_PATH,
    descriptor: {
      name: 'dashboard_contract_pages_v1_profile_target_more',
      boundTo: 'families',
      families: /** @type {const} */ ([
        ['GET', '/api/profile-files', 'ProfileTargetMoreFilesAnswer'],
        ['GET', '/api/profile-src/', 'ProfileTargetMoreSourceAnswer'],
        ['POST', '/api/profile-patch', 'ProfileTargetMorePatchAnswer'],
      ]).map(([method, route, name]) => ({
        key: `profile_target_more ${method} ${route}`,
        source: 'cases.profile_target_more',
        method,
        pathPrefix: route,
        name,
        rawPath: 'answer',
        pick: ['status', 'body'],
      })),
    },
  },
];

/** The fixture families a descriptor reads (`cases.<family>` names the family `<family>`). */
function boundFamilies(/** @type {Descriptor} */ descriptor) {
  return [
    ...new Set(
      descriptor.families.map((family) => (family.source ?? family.key).replace(/^cases\./, '')),
    ),
  ].sort();
}

/**
 * A family-bound module's identity: the SHA-256 of `<family> <HASH>` lines
 * (the backend's family hashes), so appending a family to the fixture keeps it.
 * @param {string} file @param {Descriptor} descriptor @returns {Identity}
 */
export function familyIdentity(file, descriptor) {
  const hashes = familyHashes(file);
  /** @type {Record<string, string>} */
  const bound = {};
  for (const family of boundFamilies(descriptor)) {
    const hash = hashes[family];
    if (hash === undefined) throw new Error(`The fixture ${file} has no family ${family}`);
    bound[family] = hash;
  }
  const lines = Object.entries(bound).map(([family, hash]) => `${family} ${hash}`);
  return {
    path: file,
    sha256: createHash('sha256').update(lines.join('\n'), 'utf8').digest('hex'),
    families: bound,
  };
}

export function loadContractInputs() {
  return CONTRACT_INPUTS.map(({ file, descriptor }) => {
    const loaded = loadJsonWithIdentity(file);
    return {
      fixture: loaded.fixture,
      identity:
        descriptor.boundTo === 'families' ? familyIdentity(file, descriptor) : loaded.identity,
      descriptor,
    };
  });
}

/** @param {unknown} value @returns {Record<string, unknown>} */
export function record(value) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Expected a plain fixture object');
  }
  return Object.fromEntries(Object.entries(value));
}

/** @param {unknown} fixture @param {string} key @returns {unknown} */
function atPath(fixture, key) {
  let value = fixture;
  for (const part of key.split('.')) {
    if (value === undefined) return undefined;
    value = record(value)[part];
  }
  return value;
}

/** @param {unknown} fixture @param {Descriptor} [descriptor] */
export function fixtureFamilies(fixture, descriptor = HARVEST_DESCRIPTOR) {
  return descriptor.families.flatMap((family) => {
    const value = atPath(fixture, family.source ?? family.key);
    if (value === undefined && family.optional) return [];
    /** @type {unknown[]} */
    const rows = Array.isArray(value) ? value : [];
    const samples =
      family.mode === 'rows'
        ? rows.map((sample, i) => ({ name: String(i), value: sample }))
        : Object.entries(record(value))
            .filter(
              ([, sample]) =>
                family.method === undefined ||
                record(record(sample)['request'])['method'] === family.method,
            )
            .filter(
              ([, sample]) =>
                family.pathPrefix === undefined ||
                String(record(record(sample)['request'])['path']).startsWith(family.pathPrefix),
            )
            .map(([name, sample]) => ({
              name,
              value:
                family.pick === undefined
                  ? sample
                  : Object.fromEntries(
                      Object.entries(record(sample)).filter(([key]) => family.pick?.includes(key)),
                    ),
            }));
    if (samples.length === 0) throw new Error(`No samples for ${family.key}`);
    return [
      {
        ...family,
        samples,
      },
    ];
  });
}

/** @param {string} value */
function identifier(value) {
  return value
    .split(/[^a-z\d]+/i)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');
}

/** @param {unknown} value */
function kind(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function plainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** @param {string} name */
function schemaName(name) {
  return name.charAt(0).toLowerCase() + name.slice(1) + 'Schema';
}

/** @param {unknown} value */
function members(value) {
  return plainObject(value) ? Object.keys(value).sort().join(', ') || '(none)' : kind(value);
}

/**
 * A family root's observations are split into envelope variants before any
 * member is inferred (R7-03): an answer `{status, body}` by its HTTP status and
 * the member set of its body, any other root by its own member set. An error
 * body therefore never makes a success member optional; each variant infers
 * optionality only from its own observations, and the family accepts the union.
 * @param {Record<string, unknown>[]} objects
 * @returns {Group[]}
 */
function envelopeGroups(objects) {
  const answers = objects.every(
    (object) => typeof object['status'] === 'number' && Object.hasOwn(object, 'body'),
  );
  /** @type {Map<string, { status: number | null, members: string, objects: Record<string, unknown>[] }>} */
  const groups = new Map();
  for (const object of objects) {
    const status = answers ? Number(object['status']) : null;
    const body = members(answers ? object['body'] : object);
    const key = `${status ?? ''}|${body}`;
    const group = groups.get(key) ?? { status, members: body, objects: [] };
    group.objects.push(object);
    groups.set(key, group);
  }
  const sorted = [...groups.values()].sort(
    (a, b) => (a.status ?? 0) - (b.status ?? 0) || a.members.localeCompare(b.members),
  );
  return sorted.map((group) => {
    const sameStatus = sorted.filter((other) => other.status === group.status);
    const ordinal = sameStatus.length > 1 ? `V${sameStatus.indexOf(group) + 1}` : '';
    return {
      objects: group.objects,
      label: group.status === null ? ordinal : `Status${group.status}${ordinal}`,
      description:
        group.status === null
          ? `members ${group.members}`
          : `status ${group.status}, body members ${group.members}`,
      tag: null,
    };
  });
}

/**
 * Below the root, observations whose `kind` takes several string values are a
 * tagged union (R7-02): each kind is inferred on its own and carries its kind as
 * a literal, so no member or vocabulary value of one kind is admitted for another.
 * @param {Record<string, unknown>[]} objects
 * @returns {Group[]}
 */
function kindGroups(objects) {
  /** @type {Map<string | null, Record<string, unknown>[]>} */
  const groups = new Map();
  for (const object of objects) {
    const tag = typeof object['kind'] === 'string' ? object['kind'] : null;
    groups.set(tag, [...(groups.get(tag) ?? []), object]);
  }
  if (groups.size < 2) return [{ objects, label: '', description: '', tag: null }];
  return [...groups]
    .sort(([a], [b]) => String(a).localeCompare(String(b)))
    .map(([tag, group]) => ({
      objects: group,
      label: tag === null ? 'KindAbsent' : `Kind${identifier(tag)}`,
      description: tag === null ? 'no string kind' : `kind ${tag}`,
      tag: tag === null ? null : { key: 'kind', value: tag },
    }));
}

/**
 * The values a vocabulary key may take in these objects: the union, over the
 * objects' tag values, of everything the fixture records for that tag at any
 * raw path the vocabulary names (F-058: the states the backend writes for each
 * command kind, wherever a command is serialized). Undefined when no vocabulary
 * applies, so the key keeps the values observed at this raw path.
 * @param {Context} context @param {string} rawPath @param {Record<string, unknown>[]} objects
 * @param {string} key
 * @returns {string[] | undefined}
 */
function vocabularyValues(context, rawPath, objects, key) {
  const domain = context.vocabularies.find(
    ({ vocabulary }) => vocabulary.key === key && vocabulary.rawPath.test(rawPath),
  );
  if (domain === undefined) return undefined;
  /** @type {Set<string>} */
  const values = new Set();
  for (const object of objects) {
    const tag = object[domain.vocabulary.tag];
    const recorded = typeof tag === 'string' ? domain.byTag.get(tag) : undefined;
    if (recorded === undefined)
      throw new Error(`No ${domain.vocabulary.tag} vocabulary for ${key} at ${rawPath}`);
    for (const value of recorded) values.add(value);
  }
  return [...values].sort();
}

/**
 * One strict object over observations of the same variant: a member is optional
 * only when some observation of this variant lacks it.
 * @param {Record<string, unknown>[]} objects @param {string} name @param {string} rawPath
 * @param {Context} context @param {Group['tag']} tag
 */
function strictObject(objects, name, rawPath, context, tag) {
  const keys = [...new Set(objects.flatMap(Object.keys))].sort();
  const fields = keys.map((key) => {
    const present = objects.filter((object) => Object.hasOwn(object, key));
    const child =
      tag !== null && key === tag.key
        ? `z.literal(${JSON.stringify(tag.value)})`
        : infer(
            present.map((object) => object[key]),
            name + '_' + identifier(key),
            `${rawPath}.${key}`,
            context,
            {
              key,
              literal:
                (key === 'version' && !rawPath.includes('.')) ||
                (key === 'status' && objects.some((object) => Object.hasOwn(object, 'body'))),
              values: vocabularyValues(context, `${rawPath}.${key}`, present, key),
            },
          );
    return `${JSON.stringify(key)}: ${child}${present.length < objects.length ? '.optional()' : ''}`;
  });
  return `z.strictObject({${fields.join(',\n')}})`;
}

/**
 * Object observations at one raw path, split into variants (envelopes at a
 * family root, kinds below it); each variant is its own declaration named by
 * the raw path plus the variant, and the raw-path name is their union.
 * @param {Record<string, unknown>[]} objects @param {string} name @param {string} rawPath
 * @param {Context} context @param {boolean} root
 */
function objectSchema(objects, name, rawPath, context, root) {
  const groups = root ? envelopeGroups(objects) : kindGroups(objects);
  const only = groups[0];
  if (groups.length === 1 && only !== undefined)
    return strictObject(only.objects, name, rawPath, context, only.tag);
  const variants = groups.map((group) => {
    const variant = `${name}_${group.label}`;
    context.declarations.push({
      name: variant,
      rawPath,
      variant: group.description,
      expression: strictObject(group.objects, variant, rawPath, context, group.tag),
    });
    return schemaName(variant);
  });
  return groups.every((group) => group.tag !== null)
    ? `z.discriminatedUnion('kind', [${variants.join(', ')}])`
    : `z.union([${variants.join(', ')}])`;
}

/**
 * All observations at exactly this raw path are merged, except that object
 * observations are first split into variants (objectSchema). Sibling routine
 * names are never pooled. Exported nested schemas/types retain that full path.
 * @param {readonly unknown[]} samples
 * @param {string} name
 * @param {string} rawPath
 * @param {Context} context
 * @param {{ key?: string, literal?: boolean, root?: boolean, values?: readonly string[] | undefined }} [options]
 * @returns {string}
 */
function infer(samples, name, rawPath, context, options = {}) {
  const nonNull = samples.filter((sample) => sample !== null);
  const kinds = [...new Set(nonNull.map(kind))].sort();
  /** @type {string[]} */
  const alternatives = [];
  for (const type of kinds) {
    const values = nonNull.filter((value) => kind(value) === type);
    if (type === 'object') {
      alternatives.push(
        objectSchema(values.map(record), name, rawPath, context, options.root === true),
      );
    } else if (type === 'array') {
      /** @type {unknown[]} */
      const elements = [];
      for (const value of values) {
        /** @type {unknown[]} */
        const array = Array.isArray(value) ? value : [];
        elements.push(...array);
      }
      alternatives.push(
        elements.length === 0
          ? 'z.array(z.unknown()) /* Never observed non-empty at this raw path. */'
          : `z.array(${infer(elements, name + '_ArrayElement', rawPath + '[]', context)})`,
      );
    } else if (options.literal && type !== 'string') {
      const literals = [...new Set(values.map((value) => JSON.stringify(value)))].sort();
      alternatives.push(
        literals.length === 1
          ? `z.literal(${literals[0]})`
          : `z.union([${literals.map((value) => `z.literal(${value})`).join(', ')}])`,
      );
    } else if (type === 'string' && DISCRIMINATOR_KEYS.has(options.key ?? '')) {
      const observed = [...new Set(values.map(String))].sort();
      const allowed = options.values ?? observed;
      const unlisted = observed.filter((value) => !allowed.includes(value));
      if (unlisted.length > 0)
        throw new Error(`Vocabulary at ${rawPath} lacks observed ${unlisted.join(', ')}`);
      alternatives.push(`z.enum(${JSON.stringify(allowed)})`);
    } else if (type === 'string' || type === 'number' || type === 'boolean') {
      alternatives.push(`z.${type}()`);
    } else {
      throw new Error(`Unsupported fixture kind ${type} at ${rawPath}`);
    }
  }
  let expression =
    alternatives.length === 0
      ? 'z.null()'
      : alternatives.length === 1
        ? (alternatives[0] ?? 'z.never()')
        : `z.union([${alternatives.join(', ')}])`;
  if (nonNull.length !== samples.length && nonNull.length !== 0) expression += '.nullable()';
  context.declarations.push({ name, rawPath, expression });
  return schemaName(name);
}

/**
 * Every object recorded at each raw path under the family roots.
 * @param {readonly { rawPath: string, samples: readonly { value: unknown }[] }[]} families
 * @param {(rawPath: string, object: Record<string, unknown>) => void} visit
 */
export function visitObjects(families, visit) {
  /** @param {string} rawPath @param {unknown} value */
  function walk(rawPath, value) {
    if (Array.isArray(value)) for (const element of value) walk(rawPath + '[]', element);
    else if (plainObject(value)) {
      visit(rawPath, value);
      for (const [key, member] of Object.entries(value)) walk(`${rawPath}.${key}`, member);
    }
  }
  for (const family of families)
    for (const sample of family.samples) walk(family.rawPath, sample.value);
}

/**
 * @param {readonly { rawPath: string, samples: readonly { value: unknown }[] }[]} families
 * @param {readonly Vocabulary[]} vocabularies
 * @returns {Context['vocabularies']}
 */
export function vocabularyDomains(families, vocabularies) {
  return vocabularies.map((vocabulary) => {
    /** @type {Map<string, Set<string>>} */
    const byTag = new Map();
    visitObjects(families, (rawPath, object) => {
      const tag = object[vocabulary.tag];
      const value = object[vocabulary.key];
      if (!vocabulary.rawPath.test(`${rawPath}.${vocabulary.key}`) || typeof tag !== 'string')
        return;
      if (typeof value !== 'string')
        throw new Error(`${vocabulary.key} at ${rawPath} is not a string`);
      byTag.set(tag, (byTag.get(tag) ?? new Set()).add(value));
    });
    if (byTag.size === 0) throw new Error(`Vocabulary ${vocabulary.rawPath} matched no object`);
    return { vocabulary, byTag };
  });
}
/** @param {string} source @param {string} file */
async function formatted(source, file) {
  return format(source, {
    ...(await resolveConfig(path.join(APP, '.prettierrc.json'))),
    filepath: file,
  });
}

/** @param {Identity} identity @param {Descriptor} [descriptor] */
export function generatedFile(identity, descriptor = HARVEST_DESCRIPTOR) {
  return path.join(
    GENERATED_DIR,
    `${descriptor.name}.${identity.sha256.slice(0, 8).toLowerCase()}.ts`,
  );
}

/** In-memory entry point shared by the CLI and synthetic/fixture drift tests.
 * @param {unknown} fixture @param {Identity} identity @param {Descriptor} [descriptor]
 */
export async function generateModule(fixture, identity, descriptor = HARVEST_DESCRIPTOR) {
  const families = fixtureFamilies(fixture, descriptor);
  const sources = (descriptor.vocabularyFrom ?? []).map(({ file, descriptor: other }) => {
    const loaded = loadJsonWithIdentity(file);
    return { identity: loaded.identity, families: fixtureFamilies(loaded.fixture, other) };
  });
  /** @type {Context} */
  const context = {
    declarations: [],
    vocabularies: vocabularyDomains(
      [...families, ...sources.flatMap((source) => source.families)],
      descriptor.vocabularies ?? [],
    ),
  };
  const { declarations } = context;
  const roots = families.map((family) => {
    const schema = infer(
      family.samples.map((sample) => sample.value),
      family.name,
      family.rawPath,
      context,
      { root: true },
    );
    return `${JSON.stringify(family.key)}: ${schema}`;
  });
  const rootNames = new Set(families.map((family) => family.name));
  const names = declarations.map((item) => item.name);
  if (new Set(names).size !== names.length) throw new Error('Raw-path schema name collision');
  // A family-bound module names its families' hashes and their combined hash (familyIdentity).
  const inputHash =
    identity.families === undefined
      ? `// Input SHA-256: ${identity.sha256.toLowerCase()}`
      : `// Input families: ${Object.entries(identity.families)
          .map(([family, hash]) => `${family} ${hash}`)
          .join(', ')}\n// Input SHA-256 of these families: ${identity.sha256.toLowerCase()}`;
  const vocabularyInputs = sources
    .map(
      ({ identity: source }) =>
        `\n// Vocabularies also from: ${path.basename(source.path)} ${source.sha256.toLowerCase()}`,
    )
    .join('');
  const text = `// DO NOT EDIT. Generated by scripts/generate-contract.mjs.
// Input name: ${path.basename(identity.path)}
${inputHash}${vocabularyInputs}
import { z } from 'zod';
export const fixtureVersionSchema = z.literal(${JSON.stringify(record(fixture)['version'])});
export type FixtureVersion = z.infer<typeof fixtureVersionSchema>;
${declarations
  .map(({ name, rawPath, variant, expression }) => {
    const schema = schemaName(name);
    const where = variant === undefined ? rawPath : `${rawPath}, variant: ${variant}`;
    const declaration = `/** Strict raw response path: ${where}. */\nexport const ${schema} = ${expression};`;
    // Only a family root gets a named type: the app imports no per-path type, and thousands of
    // unused aliases only made every typed lint/check load heavier (lint ran out of memory).
    return rootNames.has(name)
      ? `${declaration}\n/** Raw response path: ${where}. */\nexport type ${name} = z.infer<typeof ${schema}>;`
      : declaration;
  })
  .join('\n')}
/** Strict schemas keyed by fixture family, including sequence/readback samples. */
export const familySchemas: Readonly<Record<string, z.ZodType>> = {${roots.join(',\n')}};
`;
  return formatted(text, generatedFile(identity, descriptor));
}

/** @param {Map<string, string>} modules */
async function generateIndex(modules) {
  const entries = [...modules]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([file, text], i) => {
      const sha256 = /Input SHA-256(?: of these families)?: ([a-f\d]{64})/.exec(text)?.[1];
      if (sha256 === undefined) throw new Error(`Missing generated identity in ${file}`);
      return { file, sha256, alias: `contract${i}` };
    });
  return formatted(
    `// DO NOT EDIT. Generated by scripts/generate-contract.mjs.
${entries.map(({ file, alias }) => `import * as ${alias} from './${file}';`).join('\n')}
const contracts = {${entries.map(({ sha256, alias }) => `'${sha256}': ${alias}`).join(',\n')}};
/** Unknown identities fail loudly; no default-identity fallback. */
export function contractForFixture(identity: { readonly path: string; readonly sha256: string }) {
  const found = Object.entries(contracts).find(([hash]) => hash === identity.sha256.toLowerCase());
  if (found === undefined) throw new Error('Unknown contract fixture ' + identity.path + ' (' + identity.sha256 + '); run npm run contract:generate');
  return found[1];
}
`,
    path.join(GENERATED_DIR, 'index.ts'),
  );
}

/** A hash-named generated module: `<descriptor>.<first 8 hex of its input hash>.ts`. */
const GENERATED_MODULE = /^.+\.[a-f\d]{8}\.ts$/;

/**
 * Write (or, under check, compare) a generator's outputs, and remove every hash-named module in
 * `dir` that no current input produces: a superseded module is never imported by the app, only
 * listed in the index, so keeping it would let the index answer for an input that no longer
 * exists. Under check a leftover is drift, reported with `command` like any other.
 * @param {string} dir @param {readonly (readonly [string, string])[]} outputs @param {boolean} check
 * @param {string} command
 */
export function writeGenerated(dir, outputs, check, command) {
  for (const [target, content] of outputs) {
    if (check) {
      if (!existsSync(target) || readFileSync(target, 'utf8') !== content)
        throw new Error(`Contract drift in ${target}; run ${command}`);
    } else {
      mkdirSync(path.dirname(target), { recursive: true });
      writeFileSync(target, content);
    }
  }
  const produced = new Set(
    outputs
      .filter(([target]) => path.dirname(target) === dir)
      .map(([target]) => path.basename(target)),
  );
  const stale = existsSync(dir)
    ? readdirSync(dir).filter((name) => GENERATED_MODULE.test(name) && !produced.has(name))
    : [];
  if (check && stale.length > 0)
    throw new Error(
      `Stale generated modules in ${dir} that no current input produces: ${stale.join(', ')}; run ${command}`,
    );
  for (const name of stale) rmSync(path.join(dir, name));
}

/** @param {boolean} check */
export async function generate(check) {
  /** @type {Map<string, string>} */
  const modules = new Map();
  const inputs = loadContractInputs();
  for (const { fixture, identity, descriptor } of inputs) {
    const file = generatedFile(identity, descriptor);
    modules.set(path.basename(file), await generateModule(fixture, identity, descriptor));
  }
  /** @type {[string, string][]} */
  const outputs = [...modules].map(([name, text]) => [path.join(GENERATED_DIR, name), text]);
  outputs.push([path.join(GENERATED_DIR, 'index.ts'), await generateIndex(modules)]);
  writeGenerated(GENERATED_DIR, outputs, check, 'npm run contract:generate');
  for (const { identity, descriptor } of inputs) {
    console.log(
      `${check ? 'Checked' : 'Generated'} ${generatedFile(identity, descriptor)}\nFixture: ${identity.path}\nSHA-256: ${identity.sha256}`,
    );
  }
}

if (
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  await generate(process.argv.includes('--check'));
}
