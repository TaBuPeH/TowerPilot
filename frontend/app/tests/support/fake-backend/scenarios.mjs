// @ts-check
/** Fixture choices only; HTTP routing and per-browser state belong to fake-backend.mjs. */
import { loadFixture } from '../../../scripts/contract-fixture.mjs';
import { PREVIEW_SCENARIOS } from './preview-fixtures.mjs';
import { RUNTIME_SAVES } from './runtime-fixtures.mjs';
import { EMULATOR_CONFIG_SCENARIOS } from './emulators-config-fixtures.mjs';
import { RUNTIME_CONFIG_SCENARIOS } from './runtime-config-fixtures.mjs';
import { EQUIPMENT_CONFIG_SCENARIOS } from './equipment-config-fixtures.mjs';
import { PROFILE_PATCH_FENCED_SCENARIOS } from './profile-patch-fenced-fixtures.mjs';

/**
 * GET keys are endpoint ids, pathnames, or full paths (including the query).
 * Values name dashboard `family.case` entries, or `502` for a Flask-like page.
 * Write keys are endpoint ids or `endpoint:action` / `endpoint:true|false`.
 * A write can select a fixture case and switch the session's harvest sample on success.
 * @typedef {'ok' | 'refused' | 'unknown' | 'drop' | 'hold'} WriteMode
 * `reads` switches this session's reads (GET keys as above) once the write was answered or
 * dropped after the server acted: the save the next read shows. A held write switches nothing.
 * @typedef {{ mode?: WriteMode, case?: string, after?: string, reads?: Readonly<Record<string, string>> }} WriteChoice
 * @typedef {{
 *   description: string,
 *   preview?: import('./preview-fixtures.mjs').PreviewScenario,
 *   harvest: string,
 *   get?: Readonly<Record<string, string>>,
 *   scan?: 'fresh' | 'fixture',
 *   linkedCollector?: boolean,
 *   processes?: readonly unknown[],
 *   operation?: Readonly<Record<string, unknown>>,
 *   writeMode?: WriteMode,
 *   runtimeSave?: keyof typeof RUNTIME_SAVES,
 *   writes?: Readonly<Record<string, WriteMode | WriteChoice>>
 * }} Scenario
 */

/** @type {Readonly<Record<string, string>>} */
export const DEFAULT_GETS = Object.freeze({
  status: 'status.processes_empty',
  calibrate: 'recognition_reads.calibrate_status_of_the_first_account',
  templates: 'recognition_reads.templates_of_the_first_account',
  required: 'recognition_reads.required_images_some_captured',
  modules: 'recognition_reads.module_catalogue_nothing_learned',
  scanPlan: 'recognition_reads.scan_plan_of_the_first_account',
  scanPlanPreset: 'recognition_reads.scan_plan_for_a_run',
  analysisStatus: 'screen_analysis.status_idle_nothing_was_ever_analysed',
  analysisFrame: 'screen_analysis.frame_of_a_finished_analysis_by_its_operation',
  frame: 'recognition_images.frame_png_remembered_under_the_token',
  templateImage: 'recognition_images.read_a_captured_template',
  artwork: 'recognition_images.artwork_a_text_badge_for_a_control_the_game_draws_as_text',
  battlesActive: 'battles_active.null_no_driver',
  schedule: 'schedule_status.scheduler_not_observed',
  battles: 'battles_history.empty',
  config: 'config.taps_true',
  accounts: 'accounts.none_active',
  runs: 'runs.ready',
  wizardTools: 'wizard_tools.installed',
  wizardState: 'wizard_state.after_scan',
  wizardEmulators: 'wizard_emulators.instances_with_names_and_ports',
  resolution: 'emulator_inventory.resolution_portrait_reference',
  runControls: 'run_controls.ad_gems_on_floating_off',
  readiness: 'readiness.ready_with_context',
  profileFiles: 'profile_reads.profile_files_with_a_draft_and_the_starter',
  profileSource: 'profile_reads.profile_src_valid',
  settings: 'settings.populated_account_every_module',
  runTemplates:
    'collection_first_policy.read_the_run_templates_that_carry_the_default_gathering_policies',
  vocab: 'profile_reads.vocab_ok',
  runtime: 'runtime_state.idle_server_build_matches_the_source_on_disk',
  runtimeEvents: 'runtime_diagnostics.every_event_oldest_first_with_log_coordinates',
});

/** Collection page reads shared by its save and refusal scenarios. */
const collectionGets = Object.freeze({
  config: 'config_state.read_before_the_save',
  accounts: 'accounts.none_active',
  profileSource: 'collection_policies.read_before_the_first_save',
  settings: 'settings.populated_account_every_module',
  vocab: 'profile_reads.vocab_ok',
});

/** PE4: exact recorded Collection writes, each starting at its recorded revision. */
const profileTargetCollectionGets = Object.freeze({
  ...collectionGets,
  config: 'profile_target.inactive_editor_context_before_save',
  profileFiles: 'profile_target.same_account_inactive_target_listed',
  settings: 'profile_target_collection_settings',
});

/** Emulators page reads shared by its connection and tools scenarios. */
const emulatorsGets = Object.freeze({
  config: 'config_state.read_before_the_save',
  accounts: 'accounts.none_active',
  settings: 'settings.populated_account_every_module',
  wizardTools: 'emulator_tools.tools_idle_before_any_install',
  // The scan whose configured address (fake:1) is the one the config read names.
  wizardEmulators: 'wizard_emulators.instances_with_names_and_ports',
});

/** Recognition reads and the recorded reads that settle each successful write. */
const recognitionGets = Object.freeze({
  config: 'config_state.read_before_the_save',
  status: 'status.processes_empty',
  battlesActive: 'battles_active.null_no_driver',
  runs: 'runs.ready',
});

const recognitionWrites = Object.freeze({
  calibrateStart: {
    case: 'calibrate_writes.start_bootstrap_with_the_action_capture_flows',
    reads: { calibrate: 'calibrate_writes.read_the_running_calibration_by_operation' },
  },
  calibrateObserve: {
    case: 'calibrate_writes.observe_watch_30_seconds',
    reads: { calibrate: 'calibrate_writes.read_the_running_observation_by_operation' },
  },
  calibrateStop: {
    case: 'calibrate_writes.stop_running_calibration_asks_the_worker_to_stop',
    reads: { calibrate: 'calibrate_writes.read_the_calibration_that_was_asked_to_stop' },
  },
  calibrateApply: {
    case: 'calibrate_writes.apply_awaiting_apply_is_applied',
    reads: { calibrate: 'calibrate_writes.read_the_applied_calibration_by_operation' },
  },
  analysisStart: {
    case: 'screen_analysis.start_accepted',
    reads: {
      analysisStatus: 'screen_analysis.status_done_with_matches_and_an_ambiguous_one',
      analysisFrame: 'screen_analysis.frame_of_a_finished_analysis_by_its_operation',
    },
  },
  templateWrite: {
    case: 'recognition_images.save_a_crop_of_the_cached_frame',
    reads: {
      templates: 'recognition_images.read_the_template_list_after_the_save',
      scanPlan:
        'recognition_images.read_the_scan_plan_after_the_save_shows_the_cut_screen_and_its_verification',
      templateImage: 'recognition_images.read_the_saved_template',
    },
  },
});

/** Every harvest sample is directly selectable by its fixture name. @type {Record<string, Scenario>} */
const samples = Object.fromEntries(
  Object.keys(loadFixture().status).map((harvest) => [
    harvest,
    { description: `Harvest status.${harvest}, fresh scan and default Dashboard reads.`, harvest },
  ]),
);

/**
 * Environment E0 of spec 9.1 for every sample, as `e0_<sample>`: the sample
 * on a fresh scan with no processes, and a config read with `allow_taps` false.
 * @type {Record<string, Scenario>}
 */
const e0Samples = Object.fromEntries(
  Object.keys(loadFixture().status).map((harvest) => [
    `e0_${harvest}`,
    {
      description: `Spec 9.1 E0: harvest status.${harvest}, fresh scan, allow_taps false.`,
      harvest,
      get: { config: 'config.taps_false' },
    },
  ]),
);

/**
 * A2-7 AC1/AC3/AC5: This battle rendered from a landed r17b projection recording.
 * @param {string} recorded the case of battle_actions_projection @returns {Scenario}
 */
function actionScenario(recorded) {
  return {
    description: `This battle from battle_actions_projection.${recorded} (landed r17b).`,
    harvest: 'owned_by_run_gathers',
    get: {
      battlesActive: `r17b:battle_actions_projection.${recorded}`,
      battles: 'battles_history.ended_with_final_wave',
    },
  };
}

/** @type {Readonly<Record<string, Scenario>>} */
export const SCENARIOS = Object.freeze({
  ...PREVIEW_SCENARIOS,
  ...EQUIPMENT_CONFIG_SCENARIOS,
  ...PROFILE_PATCH_FENCED_SCENARIOS,
  ...EMULATOR_CONFIG_SCENARIOS,
  ...RUNTIME_CONFIG_SCENARIOS,
  profile_target_collection_label: {
    description: 'PE4 inactive label at its recorded revision',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second':
        'profile_target_collection_source:inactive_plan_saved_while_runner_listed',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_gather_label_saved',
        reads: {
          '/api/profile-src/second': 'profile_target_collection_source:inactive_gather_label_saved',
        },
      },
    },
  },
  profile_target_collection_copy: {
    description: 'PE4 inactive copy with the recorded value as source',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second':
        'profile_target_collection_copy_source:inactive_gather_label_saved',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_gather_copy_saved',
        reads: {
          '/api/profile-src/second': 'profile_target_collection_source:inactive_gather_copy_saved',
        },
      },
    },
  },
  profile_target_collection_create: {
    description: 'PE4 inactive template creation',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second': 'profile_target_collection_source:inactive_gather_copy_saved',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_gather_template_created',
        reads: {
          '/api/profile-src/second':
            'profile_target_collection_source:inactive_gather_template_created',
        },
      },
    },
  },
  profile_target_collection_delete: {
    description: 'PE4 inactive unused delete without value',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second':
        'profile_target_collection_source:inactive_gather_template_created',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_unused_gather_deleted',
        reads: {
          '/api/profile-src/second':
            'profile_target_collection_source:inactive_unused_gather_deleted',
        },
      },
    },
  },
  profile_target_collection_chores: {
    description: 'PE4 inactive chores',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second': 'profile_target_collection_source:inactive_unused_gather_deleted',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_chores_saved',
        reads: {
          '/api/profile-src/second': 'profile_target_collection_source:inactive_chores_saved',
        },
      },
    },
  },
  profile_target_collection_changed: {
    description: 'PE4 click-time active context change',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      config: 'profile_target.read_config_after_editor_active_profile_switch',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
    },
  },
  profile_target_collection_stale: {
    description: 'PE4 path-independent recorded stale refusal',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
    },
    // Modified case: the producer requests plan; its stale refusal applies to label too.
    writes: { profilePatch: { case: 'profile_target.inactive_target_stale_revision' } },
  },
  profile_target_collection_unknown: {
    description: 'PE4 failed inactive label then positive target read',
    harvest: 'off',
    get: {
      ...profileTargetCollectionGets,
      '/api/profile-src/second':
        'profile_target_collection_source:inactive_plan_saved_while_runner_listed',
    },
    // Modified case: the producer requests plan; the failure envelope applies to label too.
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_written_then_answer_failed',
        reads: {
          '/api/profile-src/second': 'profile_target_collection_source:inactive_gather_label_saved',
        },
      },
    },
  },
  shopping_library_create: {
    description: 'SQ9 empty queue creation with explicit reserve and choices.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_queue.shopping_library_read_before_any_edit',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        case: 'shopping_queue.shopping_policy_created',
        reads: {
          profileSource: 'shopping_library_after:shopping_policy_created',
        },
      },
    },
  },
  shopping_library_copy: {
    description: 'SQ9 independent policy copy at its recorded starting revision.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_library_before:shopping_policy_copied',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        case: 'shopping_queue.shopping_policy_copied',
        reads: {
          profileSource: 'shopping_library_after:shopping_policy_copied',
        },
      },
    },
  },
  shopping_library_v2: {
    description: 'SQ9 shared weighted policy update with keyboard order.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_library_before:shopping_shared_v2_updated',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        case: 'shopping_queue.shopping_shared_v2_updated',
        reads: {
          profileSource: 'shopping_library_after:shopping_shared_v2_updated',
        },
      },
    },
  },
  shopping_library_legacy: {
    description: 'SQ10 legacy mapping, vocabulary and the recorded clicks-zero refusal.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_library_draft:shopping_legacy_clicks_invalid',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        mode: 'refused',
        case: 'shopping_queue.shopping_legacy_clicks_invalid',
      },
    },
  },
  shopping_library_delete: {
    description: 'SQ9 confirmed delete of an unused shared policy; used delete remains disabled.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_queue.shopping_library_read_before_any_edit',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        case: 'shopping_queue.shopping_policy_deleted_unused',
        reads: {
          profileSource: 'shopping_library_after:shopping_policy_deleted_unused',
        },
      },
    },
  },
  shopping_library_unknown: {
    description: 'SQ9 save answer fails after the write; the old read cannot settle it.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_queue.shopping_library_read_before_any_edit',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      profilePatch: {
        mode: 'unknown',
        case: 'shopping_queue.shopping_shared_reply_failed',
      },
    },
  },
  shopping_library_readback: {
    description: 'SQ9 later exact source read settles the existing Unknown save.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_queue.shopping_shared_read_after_unknown',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
  },
  shopping_library_context: {
    description: 'SQ9 read only initial context, used by a browser click-time race probe.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'shopping_queue.shopping_library_read_before_any_edit',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
  },
  shopping_live_save: {
    description: 'SQ7 full live plan recorded; browser controls the later applied read.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      battlesActive: 'shopping_queue.shopping_plan_read_requested',
    },
    writes: { liveControls: { case: 'shopping_queue.shopping_plan_accepted' } },
  },
  shopping_live_unknown: {
    description: 'SQ12 shopping plan admission reply is unknown; no automatic replay.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      battlesActive: 'shopping_queue.shopping_plan_unknown_read',
    },
    writes: { liveControls: { case: 'shopping_queue.shopping_plan_reply_unknown' } },
  },
  shopping_live_replay: {
    description: 'SQ12 explicit identical shopping plan replay answers from the record.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      battlesActive: 'shopping_queue.shopping_plan_unknown_read',
    },
    writes: { liveControls: { case: 'shopping_queue.shopping_plan_identical_replay' } },
  },
  shopping_live_gated: {
    description: 'SQ7 SQ8 an unapplied plan blocks saves and Try once.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      battlesActive: 'shopping_queue.shopping_plan_read_requested',
    },
  },
  shopping_live_legacy: {
    description: 'SQ15 legacy driver offers no live queue creation or conversion.',
    harvest: 'off',
    get: { battlesActive: 'battles_active.legacy_shopping_plan' },
  },
  shopping_live_whole: {
    description: 'SQ8 a single whole-shopping attempt is recorded.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      // A recorded battle read (a write answer is not one); the browser spec supplies its
      // labelled pre-click read of this world (admission undone, tests/e2e/runs-shopping-live.spec.ts).
      battlesActive: 'shopping_queue.shopping_upgrade_completed_without_purchase',
    },
    writes: { actions: { case: 'shopping_queue.shopping_whole_accepted' } },
  },
  shopping_live_attack: {
    description: 'SQ8 a single attack-page shopping attempt is recorded.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      // A recorded battle read (a write answer is not one); the browser spec supplies its
      // labelled pre-click read of this world (admission undone, tests/e2e/runs-shopping-live.spec.ts).
      battlesActive: 'shopping_queue.shopping_upgrade_completed_without_purchase',
    },
    writes: { actions: { case: 'shopping_queue.shopping_attack_accepted' } },
  },
  shopping_live_defense: {
    description: 'SQ8 a single defense-page shopping attempt is recorded.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      // A recorded battle read (a write answer is not one); the browser spec supplies its
      // labelled pre-click read of this world (admission undone, tests/e2e/runs-shopping-live.spec.ts).
      battlesActive: 'shopping_queue.shopping_upgrade_completed_without_purchase',
    },
    writes: { actions: { case: 'shopping_queue.shopping_defense_accepted' } },
  },
  shopping_live_utility: {
    description: 'SQ8 a single utility-page shopping attempt is recorded.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      // A recorded battle read (a write answer is not one); the browser spec supplies its
      // labelled pre-click read of this world (admission undone, tests/e2e/runs-shopping-live.spec.ts).
      battlesActive: 'shopping_queue.shopping_upgrade_completed_without_purchase',
    },
    writes: { actions: { case: 'shopping_queue.shopping_utility_accepted' } },
  },
  shopping_live_upgrade: {
    description: 'SQ8 a single included upgrade attempt is recorded.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      battlesActive: 'shopping_queue.shopping_upgrade_requested',
    },
    writes: { actions: { case: 'shopping_queue.shopping_upgrade_accepted' } },
  },
  shopping_saved_create: {
    description: 'SQ1: create stages locally, explicit choices then empty queue save.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'run_controls_save.read_before_the_plan_save',
    },
    writes: {
      runControlsSave: {
        case: 'shopping_queue.shopping_empty_queue_saved',
        reads: { runControls: 'shopping_queue.shopping_empty_queue_saved' },
      },
    },
  },
  shopping_saved_conversion: {
    description: 'SQ2: legacy review, stage, type reserve and explicitly save.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'shopping_queue.shopping_legacy_list_preview',
    },
    writes: {
      runControlsSave: {
        case: 'shopping_queue.shopping_reviewed_conversion_saved',
        reads: { runControls: 'shopping_queue.shopping_read_after_the_reviewed_conversion' },
      },
    },
  },
  shopping_saved_edit: {
    description: 'SQ3-SQ7: full weighted reordered queue saved for farm only.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'shopping_queue.shopping_read_before_any_save',
      '/api/run-controls?blueprint=other': 'shopping_queue.shopping_other_run_after_private_save',
    },
    writes: {
      runControlsSave: {
        case: 'shopping_queue.shopping_weighted_cadence_order_saved',
        reads: { '/api/run-controls?blueprint=farm': 'shopping_queue.shopping_read_after_save' },
      },
    },
  },
  shopping_saved_unknown: {
    description:
      'SQ12: recorded text 500; unchanged read retains Unknown until explicit check after readback recovers.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'shopping_queue.shopping_read_before_the_unanswered_save',
    },
    writes: { runControlsSave: { case: 'shopping_queue.shopping_save_reply_failed' } },
  },
  shopping_saved_unknown_read: {
    description: 'SQ12: later matching read settles original Unknown, never another POST.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'shopping_queue.shopping_read_after_unknown_save',
    },
  },
  shopping_saved_refused: {
    description: 'SQ12: recorded invalid weight refusal keeps exact raw draft.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runControls: 'shopping_queue.shopping_read_before_any_save',
    },
    writes: {
      runControlsSave: {
        case: 'shopping_queue.shopping_invalid_weight_refused',
        reads: { runControls: 'shopping_queue.shopping_read_after_the_refusal_nothing_changed' },
      },
    },
  },

  runs_entry_saved: {
    description: 'RL3: one entry field saved, followed by the recorded saved profile.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'run_entry_patch.read_before_the_first_save',
    },
    writes: {
      profilePatch: {
        case: 'run_entry_patch.saved_label',
        reads: { profileSource: 'run_entry_patch.read_after_the_saves_holds_every_change' },
      },
    },
  },
  runs_entry_refused: {
    description:
      'RL3: the profile disappeared after drawing; its recorded refusal keeps the draft.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'run_entry_patch.read_before_the_first_save',
    },
    writes: { profilePatch: { case: 'run_entry_patch.unknown_profile_404' } },
  },
  runs_add_coin: {
    description: 'RL4: add a coin run with both guards, then read the returned profile.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'run_templates_add_fenced.read_before_the_first_add',
      runTemplates: 'run_templates_add.read_the_recipes_the_page_offers',
    },
    writes: {
      runTemplatesAdd: {
        case: 'run_templates_add_fenced.added_with_the_matching_revision_and_context',
      },
    },
  },
  runs_add_refused: {
    description: 'RL4: recorded revision conflict, with the draft kept and a profile read offered.',
    harvest: 'off',
    get: {
      config: 'run_templates_add_fenced.read_config_the_page_context_comes_from',
      profileSource: 'run_templates_add_fenced.read_before_the_first_add',
      runTemplates: 'run_templates_add.read_the_recipes_the_page_offers',
    },
    writes: {
      runTemplatesAdd: { case: 'run_templates_add_fenced.stale_revision_409_nothing_written' },
    },
  },
  ...samples,
  ...e0Samples,
  recognition_fence_saved_changed: {
    description: 'R-FENCE: the four-field apply fence refuses the stale page; nothing applied.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      config: 'recognition_context_fence.read_config_the_page_context_comes_from',
      calibrate: 'recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration',
      profileRead:
        'recognition_context_fence.read_the_profile_after_the_malformed_requests_nothing_was_applied',
    },
    writes: {
      calibrateApply: {
        case: 'recognition_context_fence.apply_saved_composed_for_alpha_is_refused_after_the_switch',
      },
    },
  },
  recognition_fence_saved_matching: {
    description: 'R-FENCE: matching saved apply writes only the reviewed profile.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      config: 'recognition_context_fence.read_config_the_page_context_comes_from',
      calibrate: 'recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration',
      profileRead:
        'recognition_context_fence.read_the_profile_after_the_malformed_requests_nothing_was_applied',
    },
    writes: {
      calibrateApply: {
        case: 'recognition_context_fence.apply_saved_with_the_matching_context_is_applied',
        reads: {
          profileRead: 'recognition_context_fence.read_the_profile_after_the_matching_apply',
        },
      },
    },
  },
  recognition: {
    description: 'Recognition idle with recorded capture, observation, analysis and crop writes.',
    harvest: 'off',
    get: recognitionGets,
    writes: recognitionWrites,
  },
  recognition_running: {
    description: 'Recognition calibration running; Stop requests cooperative cancellation.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'calibrate_writes.read_the_running_calibration_by_operation',
    },
    writes: { calibrateStop: recognitionWrites.calibrateStop },
  },
  recognition_awaiting_apply: {
    description: 'Recognition sealed evidence awaits Apply.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    },
    writes: { calibrateApply: recognitionWrites.calibrateApply },
  },
  recognition_apply_refused: {
    description: 'Recognition Apply refuses a stale operation revision.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    },
    writes: { calibrateApply: { case: 'calibrate_writes.apply_stale_revision_409' } },
  },
  recognition_apply_lost: {
    description: 'Recognition Apply acts but its answer is lost; read the applied operation.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    },
    writes: { calibrateApply: { ...recognitionWrites.calibrateApply, mode: 'drop' } },
  },
  recognition_setup_finished: {
    description: 'Recognition setup battle finished and Home was verified with target evidence.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate:
        'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
    },
  },
  recognition_failed: {
    description: 'Recognition failed capture retains its error and sealed evidence.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate:
        'recognition_reads.calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
    },
  },
  recognition_aborted: {
    description: 'Recognition capture was aborted by a person; partial evidence remains.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate:
        'recognition_reads.calibrate_status_aborted_by_a_person_keeps_the_partial_evidence',
    },
  },
  recognition_restore_pending: {
    description: 'Recognition original modules still need restoration.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate:
        'recognition_reads.calibrate_status_restoration_required_with_the_restore_file_present',
    },
  },
  recognition_artifact_error: {
    description: 'Recognition sealed report changed and is unavailable.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate:
        'recognition_reads.calibrate_status_report_changed_after_it_was_sealed_is_unavailable',
    },
  },
  recognition_taps_off: {
    description: 'Recognition tapping is disabled; starting calibration is refused.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      config: 'config.taps_false',
      calibrate: 'recognition_reads.calibrate_status_taps_not_allowed',
    },
    writes: { calibrateStart: { case: 'calibrate_writes.start_taps_off_409' } },
  },
  recognition_owned: {
    description: 'Recognition a listed runner owns the device; Start and Observe are refused.',
    harvest: 'off',
    get: { ...recognitionGets, status: 'status.processes_two_rows_same_runner' },
    writes: {
      calibrateStart: { case: 'calibrate_writes.start_while_a_run_owns_the_device_409' },
      calibrateObserve: { case: 'calibrate_writes.observe_while_a_run_owns_the_device_409' },
    },
  },
  recognition_live_battle: {
    description: 'Recognition a recorded active battle is present.',
    harvest: 'off',
    get: { ...recognitionGets, battlesActive: 'battles_active.runtime_ad_orbit_shopping' },
  },
  recognition_images_missing: {
    description: 'Recognition Start is refused because navigation images are missing.',
    harvest: 'off',
    get: recognitionGets,
    writes: {
      calibrateStart: { case: 'calibrate_writes.start_without_the_navigation_images_409' },
    },
  },
  recognition_start_lost: {
    description: 'Recognition Start is recorded and loses its answer; read the latest operation.',
    harvest: 'off',
    get: recognitionGets,
    writes: {
      calibrateStart: {
        case: recognitionWrites.calibrateStart.case,
        mode: 'drop',
        reads: {
          calibrate:
            'calibrate_writes.read_the_latest_calibration_of_the_context_after_a_lost_answer',
        },
      },
    },
  },
  recognition_observe_refused: {
    description: 'Recognition Observe is refused while a calibration runs.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'calibrate_writes.read_the_running_calibration_by_operation',
    },
    writes: {
      calibrateObserve: { case: 'calibrate_writes.observe_while_a_calibration_is_running_409' },
    },
  },
  recognition_untracked: {
    description: 'Recognition shared observations have no tracked operation and can be applied.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      calibrate: 'recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration',
    },
    writes: {
      calibrateApply: { case: 'calibrate_writes.apply_saved_observations_of_no_operation' },
    },
  },
  recognition_analysis: {
    description: 'Recognition analysis completed with matches and an ambiguous match.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      analysisStatus: 'screen_analysis.status_done_with_matches_and_an_ambiguous_one',
    },
    writes: { analysisStart: recognitionWrites.analysisStart },
  },
  recognition_analysis_launch_failed: {
    description:
      'Recognition analysis launch receipt followed by the recorded status of the same failed operation.',
    harvest: 'off',
    get: recognitionGets,
    writes: {
      analysisStart: {
        case: 'screen_analysis.start_retry_of_a_failed_start_answers_the_failed_operation',
        reads: { analysisStatus: 'screen_analysis.read_the_operation_of_the_failed_start' },
      },
    },
  },
  recognition_analysis_no_matches: {
    description:
      'An existing analysis succeeded without matches; no launch in this browser session.',
    harvest: 'off',
    get: { ...recognitionGets, analysisStatus: 'screen_analysis.status_done_without_any_match' },
  },
  recognition_analysis_ambiguous_screen: {
    description:
      'An existing analysis could not tell two screens apart; no launch in this browser session.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      analysisStatus: 'screen_analysis.status_done_on_a_screen_that_could_not_be_told_apart',
    },
  },
  recognition_analysis_failed: {
    description: 'Recognition analysis failed; a failed worker launch is settled by a read.',
    harvest: 'off',
    get: {
      ...recognitionGets,
      analysisStatus: 'screen_analysis.status_failed_operation_is_an_error_with_its_message',
      analysisFrame: 'screen_analysis.frame_of_an_analysis_with_no_saved_screenshot_409',
    },
    writes: {
      analysisStart: {
        case: 'screen_analysis.start_when_the_worker_cannot_be_started_is_a_text_500',
        reads: { analysisStatus: 'screen_analysis.read_the_operation_of_the_failed_start' },
      },
    },
  },
  recognition_crop_refused: {
    description: 'Recognition crop uses an expired cached frame and is refused.',
    harvest: 'off',
    get: recognitionGets,
    writes: {
      templateWrite: { case: 'recognition_images.save_from_a_frame_that_left_the_cache_409' },
    },
  },
  recognition_crop_exists: {
    description: 'Recognition crop refuses to replace an existing image without consent.',
    harvest: 'off',
    get: recognitionGets,
    writes: {
      templateWrite: {
        case: 'recognition_images.save_over_an_existing_template_without_the_overwrite_flag_409',
      },
    },
  },
  recognition_frame_failed: {
    description: 'Recognition live frame capture answers the recorded 502 page.',
    harvest: 'off',
    get: { ...recognitionGets, frame: 'recognition_images.frame_png_capture_failed_502' },
  },
  runtime: {
    description: 'Runtime settings, source snapshot and recorded diagnostic events.',
    harvest: 'off',
    get: { config: 'config_state.read_before_the_save' },
  },
  runtime_save_loop: {
    description: 'Runtime instance observation settings save and recorded readback.',
    harvest: 'off',
    get: { config: 'config_state.read_before_the_save', settings: RUNTIME_SAVES.loop.before },
    runtimeSave: 'loop',
  },
  runtime_save_tap: {
    description: 'Runtime machine tap settings save and recorded readback.',
    harvest: 'off',
    get: { config: 'config_state.read_before_the_save', settings: RUNTIME_SAVES.tap.before },
    runtimeSave: 'tap',
  },
  runtime_source_changed: {
    description: 'Runtime source changed after load.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runtime: 'runtime_state.server_started_before_the_source_changed_restart_needed',
    },
  },
  runtime_worker_unknown: {
    description: 'Worker identity cannot be verified.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runtime: 'runtime_state.worker_that_cannot_be_verified_is_unknown_not_dead',
    },
  },
  runtime_truncated: {
    description: 'Malformed and truncated bounded diagnostic scan.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runtimeEvents:
        'runtime_diagnostics.malformed_lines_are_counted_and_a_half_written_last_line_is_truncation',
    },
  },
  runtime_read_errors: {
    description: 'Some diagnostic files could not be read.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runtimeEvents:
        'runtime_diagnostics.one_file_cannot_be_read_the_others_are_returned_and_the_error_is_listed',
    },
  },
  runtime_writer_failure: {
    description: 'Dashboard writer reports failed writes.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      runtimeEvents: 'runtime_diagnostics.writer_health_reports_failed_writes',
    },
  },
  emulators: {
    description: 'Emulators page: adopt a suggested VM and install connection tools.',
    harvest: 'off',
    get: emulatorsGets,
    writes: {
      adopt: {
        case: 'emulator_actions.adopt_a_serial_saved_and_connected',
        reads: {
          config: 'emulator_actions.config_after_the_adopt_names_the_serial',
          wizardState: 'emulator_actions.wizard_state_after_the_adopt_holds_the_receipt',
        },
      },
      launch: { case: 'emulator_actions.launch_a_vm_no_instance_uses_skips_the_boot_pipeline' },
      toolsInstall: {
        case: 'emulator_tools.install_accepted_202_the_download_starts',
        reads: { wizardTools: 'emulator_tools.tools_installed_after_the_install' },
      },
    },
  },
  emulators_not_connected: {
    description: 'Emulators page: adoption saves the serial but does not connect it.',
    harvest: 'off',
    get: emulatorsGets,
    writes: {
      adopt: {
        case: 'emulator_actions.adopt_saved_but_not_connected',
        reads: { config: 'emulator_actions.config_after_the_adopt_names_the_serial' },
      },
    },
  },
  emulators_two_running: {
    description: 'Emulators page: two VMs are running; a stale adoption is refused.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardEmulators:
        'emulator_inventory.two_running_vms_the_configured_one_is_down_no_automatic_choice',
    },
    writes: { adopt: { case: 'emulator_actions.adopt_the_instance_changed_since_the_scan' } },
  },
  emulators_placeholder: {
    description: 'Emulators page: no emulator serial has been configured.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardEmulators: 'emulator_inventory.no_serial_configured_yet_placeholder',
    },
    writes: {
      adopt: {
        case: 'emulator_actions.adopt_a_serial_saved_and_connected',
        reads: { config: 'emulator_actions.config_after_the_adopt_names_the_serial' },
      },
    },
  },
  emulators_owned: {
    description: 'Emulators page: a runner owns the device and connection writes are refused.',
    harvest: 'off',
    get: emulatorsGets,
    writes: {
      adopt: { case: 'emulator_actions.adopt_refused_while_a_runner_owns_the_device' },
      launch: { case: 'emulator_actions.launch_refused_while_a_runner_owns_the_device' },
      reconnect: { case: 'reconnect.refused_while_a_runner_is_listed' },
    },
  },
  emulators_bluestacks: {
    description:
      'Recorded BlueStacks inventory: Prepare waits while any HD-Player runs; ADB is global.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardEmulators:
        'emulator_bluestacks_inventory.one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok',
    },
    writes: {
      prepare: {
        case: 'emulator_bluestacks_inventory.prepare_the_stopped_instance_is_refused_too_while_another_instance_runs',
      },
    },
  },
  emulators_bluestacks_runner_listed: {
    description: 'Same recorded BlueStacks inventory after a status read lists a runner.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardEmulators:
        'emulator_bluestacks_inventory.one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok',
      status: 'status.processes_two_rows_same_runner',
    },
  },
  emulators_bluestacks_prepare_modified: {
    description:
      'Browser supplies labelled all-stopped modification; Prepare answer and readback are verbatim r11.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardEmulators:
        'emulator_bluestacks_inventory.one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok',
    },
    writes: {
      prepare: {
        case: 'emulator_bluestacks_inventory.prepare_the_stopped_instance_once_nothing_runs',
        reads: {
          wizardEmulators:
            'emulator_bluestacks_inventory.read_after_the_prepare_display_ok_and_adb_on',
        },
      },
    },
  },
  emulators_launch_failed: {
    description: 'Emulators page: the manager reports a failed launch in an HTTP 200 answer.',
    harvest: 'off',
    get: emulatorsGets,
    writes: { launch: { case: 'emulator_actions.launch_the_manager_reported_a_failure' } },
  },
  emulators_launch_lost: {
    description: 'Emulators page: a launch is recorded but its answer is lost.',
    harvest: 'off',
    get: emulatorsGets,
    writes: { launch: { mode: 'drop' } },
  },
  emulators_scan_error: {
    description: 'Emulators page: discovery answers its recorded scan error.',
    harvest: 'off',
    get: { ...emulatorsGets, wizardEmulators: 'wizard_emulators.scan_error' },
  },
  emulators_none: {
    description: 'Emulators page: discovery finds no emulators.',
    harvest: 'off',
    get: { ...emulatorsGets, wizardEmulators: 'wizard_emulators.none' },
  },
  emulators_tools_downloading: {
    description: 'Emulators page: connection tools are downloading with recorded progress.',
    harvest: 'off',
    get: { ...emulatorsGets, wizardTools: 'emulator_tools.tools_downloading_with_progress' },
  },
  emulators_tools_error: {
    description: 'Emulators page: the connection tools installation failed.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      wizardTools: 'emulator_tools.tools_error_state_after_a_failed_install',
    },
  },
  emulators_resolution_landscape: {
    description: 'Emulators page: the resolution check reports a landscape display.',
    harvest: 'off',
    get: {
      ...emulatorsGets,
      resolution: 'emulator_inventory.resolution_landscape_is_not_portrait',
    },
  },
  emulators_runner_listed: {
    description: 'Emulators page: the status read lists a runner, so connection changes wait.',
    harvest: 'off',
    get: { ...emulatorsGets, status: 'status.processes_two_rows_same_runner' },
  },
  emulators_reconnect_offline: {
    description: 'Emulators page: reconnect answers with the device still offline.',
    harvest: 'off',
    get: emulatorsGets,
    writes: { reconnect: { case: 'reconnect.answered_offline_after_the_retries' } },
  },
  collection: {
    description: 'Collection page: saved rewards and chores are shown on the next read.',
    harvest: 'off',
    get: collectionGets,
    writes: {
      settingsSave: {
        case: 'settings.saved_rewards',
        reads: { settings: 'settings.read_after_every_module_was_saved' },
      },
      profilePatch: {
        case: 'collection_policies.chores_reordered_and_one_switched_off',
        reads: { profileSource: 'collection_policies.read_after_the_saves_holds_every_change' },
      },
    },
  },
  collection_first_policy: {
    description: 'Collection C5: an empty policy library creates its first policy from a template.',
    harvest: 'off',
    get: {
      ...collectionGets,
      profileSource: 'collection_first_policy.read_a_profile_with_no_gathering_policies',
      settings: 'settings.profile_without_gathering_policies_has_no_gather_modules',
    },
    writes: {
      profilePatch: {
        case: 'collection_first_policy.first_policy_created_from_the_farm_template_default',
        reads: {
          profileSource: 'collection_first_policy.read_after_the_first_policy_holds_it_unbound',
        },
      },
    },
  },
  collection_starter: {
    description: 'Collection page: the starter profile has read-only profile settings.',
    harvest: 'off',
    get: {
      ...collectionGets,
      settings: 'settings.starter_profile_active_profile_modules_read_only',
    },
    writes: {
      settingsSave: { case: 'settings.starter_profile_active_a_profile_module_is_refused' },
    },
  },
  collection_save_lost: {
    description: 'Collection page: the settings save commits but its answer is lost.',
    harvest: 'off',
    get: collectionGets,
    writes: {
      settingsSave: {
        mode: 'drop',
        reads: { settings: 'settings.read_after_the_unanswered_save_holds_the_value' },
      },
    },
  },
  collection_stale: {
    description: 'Collection page: a stale settings save is refused; reads show the other save.',
    harvest: 'off',
    get: collectionGets,
    writes: {
      settingsSave: {
        case: 'settings.stale_revision_another_client_saved_first',
        reads: {
          settings: 'settings.read_after_the_stale_refusal_holds_the_other_clients_save',
        },
      },
    },
  },
  collection_policy_added: {
    description: 'Collection page: an unused gathering policy is saved in the profile.',
    harvest: 'off',
    get: collectionGets,
    writes: {
      profilePatch: {
        case: 'collection_policies.policy_added_that_no_run_uses',
        reads: { profileSource: 'collection_policies.read_after_the_saves_holds_every_change' },
      },
    },
  },
  collection_delete_in_use: {
    description: 'Collection page: deleting a policy used by a run is refused with problems.',
    harvest: 'off',
    get: collectionGets,
    writes: {
      profilePatch: { case: 'collection_policies.policy_deleted_while_a_run_uses_it' },
    },
  },
  sketch: {
    description:
      'Manual gem collector, fresh scan/publication, unresolved setup battle; Stop held.',
    harvest: 'manual_collector',
    linkedCollector: true,
    get: { status: 'status.guards_setup_and_collection_blocking_the_same_operation' },
    writes: { cancel: 'hold', 'control:kill': 'hold' },
  },
  idle: {
    description: 'Harvesting Off, no listed processes, fresh scan.',
    harvest: 'off',
  },
  cold_502: {
    description: 'Status returns a Flask-like 502 page from the first read onward.',
    harvest: 'off',
    get: { status: '502' },
  },
  no_tracked_battle: {
    description: 'Manual collector with no tracked battle in the active-battle answer.',
    harvest: 'manual_collector',
    linkedCollector: true,
  },
  active_battle_routines: {
    description: 'Active battle with ad gems, floating gems and shopping runtime reports.',
    harvest: 'owned_by_run_gathers',
    get: {
      battlesActive: 'battles_active.runtime_ad_orbit_shopping',
      battles: 'battles_history.ended_with_final_wave',
    },
  },
  a2_7_actions_idle: actionScenario('idle_ad_and_floating_gems_due_shopping_waiting'),
  a2_7_b1_live: {
    description: 'AC7 B1 live-only field, recorded receipt and applied read.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_active_two_instances' },
    writes: { liveControls: { case: 'b1:synthetic_live_field_accepted' } },
  },
  a2_7_b1_applied: {
    description: 'AC7 B1 applied live field readback.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_live_field_read_applied' },
  },
  a2_7_b1_run: {
    description: 'AC7 B1 Run now before sending, recorded receipt.',
    harvest: 'off',
    get: { battlesActive: 'b1:before_run' },
    writes: { actions: { case: 'b1:synthetic_run_now_accepted' } },
  },
  a2_7_b1_started: {
    description: 'AC7 B1 Run now started read.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_run_now_read_started' },
  },
  a2_7_b1_completed: {
    description: 'AC7 B1 Run now completed unconfirmed read.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_run_now_read_completed_unconfirmed' },
  },
  a2_7_b1_stale: {
    description: 'AC7 B1 derivative with stale projection context.',
    harvest: 'off',
    get: { battlesActive: 'b1:stale_context' },
  },
  a2_7_b1_newer: {
    description: 'AC7 B1 newer schema reading.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_projection_newer_schema' },
  },
  a2_7_b1_unknown: {
    description: 'AC7 B1 unknown reply, no automatic retry.',
    harvest: 'off',
    get: { battlesActive: 'b1:before_unknown' },
    writes: { actions: { case: 'b1:synthetic_run_now_reply_unknown' } },
  },
  a2_7_b1_unknown_read: {
    description: 'AC7 B1 matching receipt read after unknown reply.',
    harvest: 'off',
    get: { battlesActive: 'b1:synthetic_run_now_reply_unknown_read' },
  },
  a2_7_actions_finished: actionScenario('a_finished_execution_is_the_last_of_its_instance'),
  a2_7_actions_running: actionScenario('ad_window_open_is_running'),
  a2_7_actions_run_now: actionScenario('run_now_completed_links_its_execution'),
  a2_7_actions_stale: actionScenario(
    'projection_fails_later_the_last_projection_stays_and_its_stamps_stop',
  ),
  a2_7_actions_newer: actionScenario('projection_written_by_a_newer_driver_is_read_only'),
  runs_live_overrides: {
    description:
      'Runs page: an active battle with a controls change partly applied (marks Changed and Waiting).',
    harvest: 'owned_by_run_gathers',
    get: {
      battlesActive: 'battles_active.commands_controls_pending_some_paths_applied_action_completed',
    },
  },
  runs_live_applied: {
    description: 'Runs page: an active battle whose overrides are all applied (live Save open).',
    harvest: 'owned_by_run_gathers',
    get: { battlesActive: 'battles_active.commands_controls_applied_with_overrides_action_failed' },
  },
  runs_saved: {
    description:
      'Runs page: a saved run read before its first save; a save answers 200 and the read then holds it.',
    harvest: 'off',
    get: { runControls: 'run_controls_save.read_before_the_first_save' },
    writes: {
      runControlsSave: {
        case: 'run_controls_save.saved_switch_off',
        reads: { runControls: 'run_controls_save.read_after_the_save_holds_the_new_values' },
      },
    },
  },
  runs_saved_unanswered: {
    description:
      'Runs page: the save is written, then the answer fails (a text 500); the next read holds every sent value.',
    harvest: 'off',
    get: { runControls: 'run_controls_save.read_before_the_unanswered_save' },
    writes: {
      runControlsSave: {
        case: 'run_controls_save.saved_then_the_answer_failed_500',
        reads: {
          runControls: 'run_controls_save.read_after_the_unanswered_save_holds_every_sent_value',
        },
      },
    },
  },
  runs_saved_lost: {
    description:
      'Runs page: the save is written and its answer is lost on the way; the next read shows it.',
    harvest: 'off',
    get: { runControls: 'run_controls_save.read_before_the_lost_answer' },
    writes: {
      runControlsSave: {
        mode: 'drop',
        reads: { runControls: 'run_controls_save.read_after_a_lost_answer_the_save_is_there' },
      },
    },
  },
  profile_target_schedule_saved: {
    description: 'PE3: named second plan save while a runner is listed.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      '/api/profile-src/mine': 'profile_patch.read_before_the_first_save',
      status: 'status.processes_two_rows_same_runner',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_plan_saved_while_runner_listed',
        reads: {
          '/api/profile-src/second':
            'profile_target.inactive_target_after_written_then_answer_failed',
        },
      },
    },
  },
  profile_target_schedule_lost: {
    description: 'PE3: Unknown second plan save settles only from second readback.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      '/api/profile-src/mine': 'profile_patch.read_before_the_first_save',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_written_then_answer_failed',
        reads: {
          '/api/profile-src/second':
            'profile_target.inactive_target_after_written_then_answer_failed',
        },
      },
    },
  },
  profile_target_schedule_failed: {
    description: 'PE3: failed second plan write stays Unknown after unchanged readback.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      '/api/profile-src/mine': 'profile_patch.read_before_the_first_save',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_write_failed',
        reads: { '/api/profile-src/second': 'profile_target.inactive_target_after_write_failed' },
      },
    },
  },
  profile_target_schedule_stale: {
    description: 'PE3: second source revision changed before patch.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      '/api/profile-src/mine': 'profile_patch.read_before_the_first_save',
    },
    writes: { profilePatch: { case: 'profile_target.inactive_target_stale_revision' } },
  },
  profile_target_schedule_deleted: {
    description: 'PE3: second deleted after its source was read, no fallback.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      '/api/profile-src/mine': 'profile_patch.read_before_the_first_save',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_deleted_before_patch',
        reads: {
          '/api/profile-src/second': 'profile_target.inactive_target_after_deletion',
          profileFiles: 'profile_target.inactive_target_list_after_deletion',
        },
      },
    },
  },
  profile_target_abilities_negative: {
    description:
      'C-05 abilities negative state: recorded profile_target.inactive_target_stale_revision; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: { case: 'profile_target.inactive_target_stale_revision' },
    },
  },
  profile_target_abilities_uw_refused: {
    description:
      'C-05 abilities negative state: recorded ability_policies.uw_policy_deleted_while_a_run_uses_it; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'ability_policies.uw_policy_deleted_while_a_run_uses_it',
      },
    },
  },
  profile_target_abilities_rescue_refused: {
    description:
      'C-05 abilities negative state: recorded ability_policies.rescue_policy_deleted_while_a_run_uses_it; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'ability_policies.rescue_policy_deleted_while_a_run_uses_it',
      },
    },
  },
  profile_target_abilities_failed: {
    description:
      'C-05 abilities negative state: recorded profile_target.inactive_target_write_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: { case: 'profile_target.inactive_target_write_failed' },
    },
  },
  profile_target_abilities_answer_failed: {
    description:
      'C-05 abilities negative state: recorded profile_target.inactive_target_written_then_answer_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_written_then_answer_failed',
      },
    },
  },
  profile_target_abilities_lost: {
    description:
      'C-05 abilities negative state: recorded profile_target.inactive_target_written_then_answer_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        mode: 'drop',
        case: 'profile_target.inactive_target_written_then_answer_failed',
      },
    },
  },
  profile_target_runs_negative: {
    description:
      'C-05 runs negative state: recorded profile_target.inactive_target_stale_revision; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: { case: 'profile_target.inactive_target_stale_revision' },
    },
  },
  profile_target_runs_refused: {
    description:
      'C-05 runs negative state: recorded run_entry_patch.loadout_unknown_equipment; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: { case: 'run_entry_patch.loadout_unknown_equipment' },
    },
  },
  profile_target_runs_failed: {
    description:
      'C-05 runs negative state: recorded profile_target.inactive_target_write_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: { case: 'profile_target.inactive_target_write_failed' },
    },
  },
  profile_target_runs_answer_failed: {
    description:
      'C-05 runs negative state: recorded profile_target.inactive_target_written_then_answer_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_target_written_then_answer_failed',
      },
    },
  },
  profile_target_runs_lost: {
    description:
      'C-05 runs negative state: recorded profile_target.inactive_target_written_then_answer_failed; browser controls held/modified reads.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        mode: 'drop',
        case: 'profile_target.inactive_target_written_then_answer_failed',
      },
    },
  },
  profile_target_runs_saved: {
    description:
      'PE6: second entry fields, sequential recorded label/loadout patches; mine stays active.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      profileSource: 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target_runs_before_label',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      'profilePatch:label': {
        case: 'profile_target.inactive_run_entry_label_saved',
        reads: { '/api/profile-src/second': 'profile_target_runs_after_label' },
      },
      'profilePatch:loadout': {
        case: 'profile_target.inactive_run_entry_loadout_saved',
        reads: {
          '/api/profile-src/second': 'profile_target.inactive_target_read_after_save',
        },
      },
    },
  },
  profile_target_runs_context_changed: {
    description:
      'PE6: same recorded target source; the browser injects the recorded changed config at click.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      profileSource: 'run_entry_patch.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target_runs_before_label',
      vocab: 'profile_reads.vocab_ok',
    },
  },
  profile_target_abilities_uw: {
    description: 'PE5: second UW create, active mine and isolated drafts.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.inactive_chores_saved',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_uw_policy_saved',
        reads: {
          '/api/profile-src/second': 'profile_target.inactive_uw_policy_saved',
        },
      },
    },
  },
  profile_target_abilities_rescue: {
    description: 'PE5: second rescue create after the recorded UW create.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/mine': 'ability_policies.read_before_the_first_save',
      '/api/profile-src/second': 'profile_target.inactive_uw_policy_saved',
      vocab: 'profile_reads.vocab_ok',
    },
    writes: {
      profilePatch: {
        case: 'profile_target.inactive_rescue_policy_saved',
        reads: {
          '/api/profile-src/second': 'profile_target.inactive_rescue_policy_saved',
        },
      },
    },
  },
  profile_target_abilities_context_changed: {
    description: 'PE5: click reads a changed serial; no write may leave the page.',
    harvest: 'off',
    get: {
      config: 'profile_target.read_config_after_editor_serial_change',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.inactive_uw_policy_saved',
      vocab: 'profile_reads.vocab_ok',
    },
  },
  profile_target_pick: {
    description: 'PE2: edit second independently of active mine, with no writes.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
      '/api/profile-src/second': 'profile_target.same_account_inactive_target_source',
    },
  },
  profile_target_unbind: {
    description: 'PE1: successful unbind, config names the same account with no profile.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
    },
    writes: {
      profileActivate: {
        case: 'profile_target.unbind_with_matching_account_context',
        reads: { config: 'profile_target.read_after_unbind_account_context' },
      },
    },
  },
  profile_target_unbind_lost: {
    description: 'PE1: written then answer failed; only the config read settles the outcome.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
    },
    writes: {
      profileActivate: {
        case: 'profile_target.unbind_written_then_answer_failed',
        reads: { config: 'profile_target.read_after_unbind_written_then_answer_failed' },
      },
    },
  },
  profile_target_runner_refusal: {
    description: 'PE1: runner refusal leaves mine active and never resends.',
    harvest: 'off',
    get: {
      config: 'profile_target.inactive_editor_context_before_save',
      profileFiles: 'profile_target.same_account_inactive_target_listed',
    },
    writes: {
      profileActivate: {
        case: 'profile_target.unbind_refused_while_runner_listed',
        reads: { config: 'profile_target.read_after_unbind_runner_refusal' },
      },
    },
  },
  profiles: {
    description:
      'Profiles page: two local accounts, none chosen for this emulator; selecting Alpha binds it and the config then names it.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      accounts: 'accounts.none_active',
      '/api/profile-src/broken': 'profile_reads.profile_src_with_problems',
      '/api/profile-src/default': 'profile_reads.profile_src_of_the_starter',
      '/api/profile-src/theirs':
        'profile_reads.profile_src_of_the_other_accounts_profile_is_not_refused',
    },
    writes: {
      'accountsWrite:select': {
        case: 'accounts_write.select_an_existing_account',
        reads: {
          config: 'config_state.populated_accounts_and_loadouts',
          accounts: 'accounts_write.read_after_select',
        },
      },
      accountTier: {
        case: 'accounts_write.tier_saved_by_the_operator',
        reads: {
          accounts: 'accounts_write.read_after_the_tier',
          profileSource:
            'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
        },
      },
      profileActivate: { case: 'profile_files_write.activate_the_starter' },
      profileCopy: { case: 'profile_files_write.copy_one_of_your_own_profiles' },
      profilePromote: {
        case: 'profile_files_write.promotion_returns_the_problems_it_does_not_enforce_them',
        reads: { profileFiles: 'profile_files_write.files_after_the_promotion_activate_nothing' },
      },
    },
  },
  profiles_create_failed: {
    description:
      'Profiles page: creating an account writes its profile file, then the config save fails (400); the reads show the left-behind file.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      accounts: 'accounts_write.read_before_any_account',
    },
    writes: {
      'accountsWrite:create': {
        case: 'accounts_write.create_then_the_config_save_failed_leaves_the_profile_file',
        reads: {
          accounts: 'accounts_write.read_after_the_failed_create_no_account',
          profileFiles: 'accounts_write.profile_files_after_the_failed_create_list_the_orphan',
        },
      },
    },
  },
  profiles_setup: {
    description:
      'Profiles page: connection tools missing, original modules waiting to be restored, a run not ready.',
    harvest: 'off',
    get: {
      config: 'config_state.read_before_the_save',
      accounts: 'accounts.none_active',
      wizardTools: 'wizard_tools.not_installed',
      calibrate: 'calibrate_status.restore_pending',
      runs: 'runs.not_ready_image_row',
    },
  },
  profiles_copy_lost: {
    description:
      'Profiles page: a copy is written and its answer is lost on the way; the next profile list shows it.',
    harvest: 'off',
    get: { config: 'config_state.read_before_the_save', accounts: 'accounts.none_active' },
    writes: {
      profileCopy: {
        mode: 'drop',
        reads: {
          profileFiles:
            'profile_files_write.files_after_the_copy_list_it_and_copy_activates_nothing',
        },
      },
    },
  },
  profiles_owned: {
    description:
      'Profiles page: a runner owns the device; selecting an account is refused (409 device_owned).',
    harvest: 'off',
    get: { config: 'config_state.read_before_the_save', accounts: 'accounts.none_active' },
    writes: { 'accountsWrite:select': 'refused' },
  },
  runs_saved_held: {
    description:
      'Runs page: a saved run whose save is recorded and never answered (a reload while it is in flight).',
    harvest: 'off',
    get: { runControls: 'run_controls_save.read_before_the_first_save' },
    writes: { runControlsSave: 'hold' },
  },
  runs_live: {
    description:
      'Runs page: an active battle before its first live change; a change is recorded and the next read lists it.',
    harvest: 'off',
    get: { battlesActive: 'run_controls_live.read_before_the_first_change' },
    writes: {
      liveControls: {
        reads: {
          battlesActive: 'run_controls_live.read_after_the_change_lists_the_command_and_its_key',
        },
      },
    },
  },
  runs_live_reset_all: {
    description:
      'Runs page (F6-11 RL2): a battle with two applied overrides; the reset of both is recorded (200) and the next read shows none left (r12 candidate run_controls_live_reset_all).',
    harvest: 'off',
    get: {
      battlesActive: 'run_controls_live_reset_all.read_before_the_reset_two_overrides_are_in_force',
    },
    writes: {
      liveControls: {
        case: 'run_controls_live_reset_all.accepted_null_for_every_override_resets_them_together',
        reads: { battlesActive: 'run_controls_live_reset_all.read_after_every_override_was_reset' },
      },
    },
  },
  runs_live_battle_details: {
    description:
      'Runs page (F6-11 RL7): a battle whose Ad gems offer was seen and whose claim failed (r12 candidate battle_details_runtime).',
    harvest: 'off',
    get: { battlesActive: 'battle_details_runtime.ad_gems_offer_seen_and_failed_attempt' },
  },
  runs_live_floating_refusal: {
    description:
      'Runs page (F6-11 RL7): a battle whose Floating gems routine recorded a refusal (r12 candidate battle_details_runtime).',
    harvest: 'off',
    get: { battlesActive: 'battle_details_runtime.floating_gems_refusal_recorded' },
  },
  runs_live_applied_after_change: {
    description:
      'Runs page (F6-11-IR-01): an active battle before its first live change; the change is recorded (200) and the next read lists its command applied.',
    harvest: 'off',
    get: { battlesActive: 'run_controls_live.read_before_the_first_change' },
    writes: {
      liveControls: {
        case: 'run_controls_live.accepted_switch_off',
        reads: { battlesActive: 'run_controls_live.read_after_the_driver_applied_the_change' },
      },
    },
  },
  runs_live_unknown: {
    description:
      'Runs page: a live change is recorded but its read-back fails (500 command_outcome_unknown).',
    harvest: 'off',
    get: { battlesActive: 'run_controls_live.read_before_the_first_change' },
    writes: { liveControls: 'unknown' },
  },
  prepared_battle: {
    description: 'Prepared battle with no runtime observation yet.',
    harvest: 'off',
    get: { battlesActive: 'battles_active.prepared' },
  },
  setup_guard_block: {
    description: 'Unresolved setup battle blocks Start and Collect; setup failure is visible.',
    harvest: 'setup_guard_block',
    get: {
      status: 'status.guards_setup_and_collection_blocking_the_same_operation',
      calibrate: 'calibrate_status.failed_setup_unverified_final_screen',
    },
    writes: {
      'control:start': { case: 'control.start_refused_by_setup_guard' },
      'control:start_gems': { case: 'control.start_gems_refused_by_collection_guard' },
    },
  },
  x2a: {
    description:
      '9.1 X2a: status and config fail; at the click GET /api/harvest names the context.',
    harvest: 'off',
    get: { status: '502', config: '502' },
  },
  x2b: {
    description: '9.1 X2b: status and config fail; GET /api/harvest names no context either.',
    harvest: 'status_failed',
    get: { status: '502', config: '502' },
  },
  two_runners: {
    description:
      '9.1 X8: two untracked runners of one script on a fresh scan; Off; allow_taps false.',
    harvest: 'off',
    get: { status: 'status.processes_two_rows_same_runner', config: 'config.taps_false' },
    writes: { 'control:kill': 'hold' },
  },
  x9: {
    description:
      '9.1 X9: a linked process row with a malformed operation id ({pid: 9009, operation_id: 7}); Off; allow_taps false.',
    harvest: 'off',
    get: { config: 'config.taps_false' },
    processes: [{ pid: 9009, operation_id: 7 }],
  },
  x9_taps: {
    description:
      '9.1 X9 with taps allowed: the same malformed linked row ({pid: 9009, operation_id: 7}); Off; allow_taps true.',
    harvest: 'off',
    processes: [{ pid: 9009, operation_id: 7 }],
  },
  stopping_no_revision: {
    description:
      'A stopping holder whose operation carries no valid revision (S-050: Stop waits for fresh status); allow_taps false.',
    harvest: 'stopping',
    get: { config: 'config.taps_false' },
    operation: { revision: null },
  },
  runner_created_null: {
    description: '9.1 X8 row 1: an untracked runner without its start time (S-060).',
    harvest: 'off',
    get: { status: 'status.processes_untracked_created_null', config: 'config.taps_false' },
  },
  x10_owner: {
    description:
      'A Pause refused by the r6 lease holder notice (device_owned_lease, holder 4242 named).',
    harvest: 'collecting',
    get: { config: 'config.taps_false' },
    writes: { 'harvest:false': { mode: 'refused', case: 'refusals.device_owned_lease' } },
  },
  marker_failed: {
    description:
      'Turn input off answers disable_marker_write_failed (server error text shown in full).',
    harvest: 'collecting',
    writes: {
      'automationAccess:false': {
        mode: 'unknown',
        case: 'automation_access.disable_marker_write_failed',
      },
    },
  },
  refusal: {
    description: "Writes return the endpoint's recorded owner or coded refusal.",
    harvest: 'foreign_process',
    writeMode: 'refused',
  },
  unknown_writes: {
    description: 'Writes return recorded 5xx unknown outcomes (diagnostics use a Flask 500 page).',
    harvest: 'collecting',
    writeMode: 'unknown',
  },
  dropped_writes: {
    description: 'Writes are recorded, then their sockets are destroyed without an answer.',
    harvest: 'collecting',
    writeMode: 'drop',
  },
});
