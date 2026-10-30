// @ts-check
const tournament = {
  config: 'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
  settings: 'settings_modules.read_after_saved_run_defaults',
  profileSource: 'ability_policies.read_before_the_first_save',
};
const equipment = {
  config: 'loadout_patch.read_before_the_first_save',
  settings: 'settings.populated_account_every_module',
  profileSource: 'ability_policies.read_before_the_first_save',
};
/** @type {Readonly<Record<string, import('./scenarios.mjs').Scenario>>} */
export const EQUIPMENT_CONFIG_SCENARIOS = {
  ...Object.fromEntries(
    [
      [
        'add',
        'read_before_only_the_add_list_is_emptied',
        'saved_tournament_cards_with_only_the_add_list_empty_the_drop_list_is_kept',
        'read_after_only_the_add_list_was_emptied',
      ],
      [
        'drop',
        'read_before_only_the_drop_list_is_emptied',
        'saved_tournament_cards_with_only_the_drop_list_empty_the_add_list_is_kept',
        'read_after_only_the_drop_list_was_emptied',
      ],
    ].map(([field, before, saved, after]) => [
      `config_fields_equipment_empty_${field}`,
      {
        description: `C15-6/7 C-03 empty only ${field}, keeping the other list`,
        harvest: 'off',
        get: { ...tournament, settings: `settings_modules_more.${before}` },
        writes: {
          settingsSave: {
            case: `settings_modules_more.${saved}`,
            reads: { settings: `settings_modules_more.${after}` },
          },
        },
      },
    ]),
  ),
  ...Object.fromEntries(
    [
      'without_blueprints',
      'with_blueprints_null',
      'with_blueprints_a_list',
      'with_an_empty_blueprints_mapping',
    ].map((shape) => [
      `config_fields_equipment_${shape}`,
      {
        description: `C15-8 r12f ${shape} defers to recorded server refusal`,
        harvest: 'off',
        get: {
          ...equipment,
          config: 'settings_modules_more_2.read_config_after_the_refusals_nothing_changed',
          profileSource: `settings_modules_more_2.profile_src_${shape}`,
        },
        writes: {
          loadoutPatch: {
            case: `settings_modules_more_2.restore_refused_for_a_profile_${shape}`,
            reads: {
              config: 'settings_modules_more_2.read_config_after_the_refusals_nothing_changed',
            },
          },
        },
      },
    ]),
  ),
  config_fields_equipment_malformed: {
    description: 'C15-6/7 r12f save sibling list without sending malformed drop entries',
    harvest: 'off',
    get: {
      ...tournament,
      settings: 'settings_modules_more_2.read_with_malformed_tournament_cards',
    },
    writes: {
      settingsSave: {
        case: 'settings_modules_more_2.saved_tournament_cards_add_list_only_the_malformed_drop_list_stays',
        reads: {
          settings:
            'settings_modules_more_2.read_after_the_add_list_was_saved_the_malformed_drop_list_remains',
        },
      },
    },
  },
  config_fields_equipment_lists_lost: {
    description: 'C15-6/7 C-04 recorded tournament save with its reply lost; unchanged reads',
    harvest: 'off',
    get: tournament,
    writes: { settingsSave: { mode: 'drop', case: 'settings_modules.saved_tournament_cards' } },
  },
  config_fields_equipment_cards_lost: {
    description: 'C15-8/9 C-04 recorded restore save with its reply lost; unchanged reads',
    harvest: 'off',
    get: equipment,
    writes: { loadoutPatch: { mode: 'drop', case: 'loadout_restore.saved_cards_restore' } },
  },
  config_fields_equipment_lists_failed: {
    description: 'C15-6/7 C-04 genuine failed save reply, no automatic resend',
    harvest: 'off',
    get: tournament,
    writes: {
      settingsSave: { mode: 'unknown', case: 'settings.saved_then_the_answer_failed_500' },
    },
  },
  config_fields_equipment_cards_failed: {
    description: 'C15-8/9 C-04 genuine failed save reply, no automatic resend',
    harvest: 'off',
    get: equipment,
    writes: {
      loadoutPatch: { mode: 'unknown', case: 'loadout_patch.written_then_the_answer_failed_500' },
    },
  },
  config_fields_equipment_empty: {
    description: 'C15-6/7 C-03 recorded empty-list save/readback',
    harvest: 'off',
    get: {
      ...tournament,
      settings: 'settings_modules_more.read_before_saved_tournament_cards_empty_lists',
    },
    writes: {
      settingsSave: {
        case: 'settings_modules_more.saved_tournament_cards_empty_lists',
        reads: { settings: 'settings_modules_more.read_after_saved_tournament_cards_empty_lists' },
      },
    },
  },
  config_fields_equipment_lists: {
    description: 'C15-6/7 ordered tournament lists, recorded save and readback',
    harvest: 'off',
    get: tournament,
    writes: {
      settingsSave: {
        case: 'settings_modules.saved_tournament_cards',
        reads: { settings: 'settings_modules.read_after_saved_tournament_cards' },
      },
    },
  },
  config_fields_equipment_invalid: {
    description: 'C15-6/7 genuine server invalid-list refusal retains the draft',
    harvest: 'off',
    get: tournament,
    // The UI refuses non-string members locally. Inject the actual recording's refusal
    // after a valid-string submission to prove server error display and draft retention.
    writes: {
      settingsSave: {
        mode: 'refused',
        case: 'settings_modules.invalid_string_list_tournament_cards',
      },
    },
  },
  config_fields_equipment_stale: {
    description: 'C15-6/7 genuine stale refusal retains draft',
    harvest: 'off',
    get: tournament,
    writes: {
      settingsSave: { mode: 'refused', case: 'settings_modules.stale_revision_tournament_cards' },
    },
  },
  config_fields_equipment_cards: {
    description: 'C15-8 cards restore save and config readback',
    harvest: 'off',
    get: equipment,
    writes: {
      loadoutPatch: {
        case: 'loadout_restore.saved_cards_restore',
        reads: { config: 'loadout_restore.read_after_saved_cards_restore' },
      },
    },
  },
  config_fields_equipment_modules: {
    description: 'C15-9 r12f exact restore-only first save and readback',
    harvest: 'off',
    get: { ...equipment, config: 'settings_modules_more.read_before_saved_modules_restore' },
    writes: {
      loadoutPatch: {
        case: 'settings_modules_more_2.saved_modules_restore_only',
        reads: { config: 'settings_modules_more_2.read_after_saved_modules_restore_only' },
      },
    },
  },
  config_fields_equipment_modules_manual: {
    description:
      'C15-9 C-03 change restore rows while preserving genuinely recorded manual fields; lose only the answer',
    harvest: 'off',
    get: { ...equipment, config: 'loadout_restore.read_after_saved_modules_restore' },
    writes: {
      loadoutPatch: {
        mode: 'drop',
        case: 'loadout_restore.saved_modules_restore',
      },
    },
  },
  config_fields_equipment_refused: {
    description: 'C15-9 unknown restore module refused verbatim',
    harvest: 'off',
    get: { ...equipment, config: 'loadout_restore.read_after_saved_modules_restore' },
    writes: { loadoutPatch: { mode: 'refused', case: 'loadout_restore.refused_modules_restore' } },
  },
  config_fields_equipment_unused: {
    description: 'C15-8/9 unused loadout restore disabled; ordinary save allowed',
    harvest: 'off',
    get: equipment,
  },
  config_fields_equipment_clear_unused: {
    description: 'C15-8 clearing stored restore of unused loadout stays disabled',
    harvest: 'off',
    get: {
      ...equipment,
      config: 'settings_modules_more.read_before_the_clearing_refusal',
    },
  },
  config_fields_equipment_no_profile: {
    description: 'C15-8 no active profile keeps restore save disabled',
    harvest: 'off',
    get: {
      ...equipment,
      config: 'loadout_restore_eligibility.read_after_the_no_profile_refusal_nothing_changed',
      settings: 'settings.no_active_profile_only_machine_and_emulator_modules',
    },
  },
};
