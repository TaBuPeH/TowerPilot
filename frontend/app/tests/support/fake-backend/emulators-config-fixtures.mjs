// @ts-check
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';

const fixture =
  /** @type {{cases: Record<string, Record<string, {status: number, body: Record<string, unknown>, request: {body?: Record<string, unknown>}}>>}} */ (
    loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture
  );
const cases = fixture.cases;

/** @param {string} name @returns {{status: number, body: Record<string, unknown>, request: {body?: Record<string, unknown>}}} */
export function emulatorConfigCase(name) {
  const [family, key] = name.split('.');
  const found = cases[family ?? '']?.[key ?? ''];
  if (found === undefined) throw new Error(`Missing recorded case ${name}`);
  return structuredClone(found);
}

/** The genuine predecessor of settings_modules.saved_instance_defaults. */
export function emulatorConfigBefore() {
  return emulatorConfigCase('settings_modules_more.read_before_saved_instance_defaults');
}

const base = {
  config: 'settings_switch_records.read_config_after_switching_back_every_record_as_it_was',
  settings: 'settings_modules_more.read_before_saved_instance_defaults',
  wizardEmulators: 'wizard_emulators.instances_with_names_and_ports',
};

/** @type {Readonly<Record<string, import('./scenarios.mjs').Scenario>>} */
export const EMULATOR_CONFIG_SCENARIOS = {
  config_fields_emulators_unlisted_fallback: {
    description: 'C15-2 C-03 explicitly replace the historical preset with machine fallback',
    harvest: 'off',
    get: {
      ...base,
      settings:
        'settings_modules_more.read_after_the_partial_instance_save_the_unlisted_preset_remains',
    },
    writes: {
      settingsSave: {
        case: 'settings_modules_more.instance_defaults_choosing_the_machine_fallback_replaces_the_unlisted_preset',
        reads: {
          settings: 'settings_modules_more.read_after_the_unlisted_instance_preset_was_replaced',
        },
      },
    },
  },
  config_fields_emulators_no_runnable: {
    description: 'C15-2 C-03 no runnable presets still offers explicit machine fallback',
    harvest: 'off',
    get: {
      ...base,
      settings: 'settings_modules_more.read_with_no_runnable_run_defaults_preset_options',
    },
    writes: {
      settingsSave: {
        case: 'settings_modules_more.instance_defaults_with_no_runnable_preset_still_saves_the_empty_value',
        reads: {
          settings:
            'settings_modules_more.read_after_the_empty_value_was_saved_with_no_runnable_preset',
        },
      },
    },
  },
  config_fields_emulators_defaults: {
    description: 'C15-1..3 recorded defaults save and readback',
    harvest: 'off',
    get: base,
    writes: {
      settingsSave: {
        case: 'settings_modules.saved_instance_defaults',
        reads: { settings: 'settings_modules.read_after_saved_instance_defaults' },
      },
    },
  },
  config_fields_emulators_stale: {
    description: 'C15-1 stale defaults retain the draft',
    harvest: 'off',
    get: base,
    writes: {
      settingsSave: { mode: 'refused', case: 'settings_modules.stale_revision_instance_defaults' },
    },
  },
  config_fields_emulators_invalid: {
    description: 'C15-2 historical unsupported preset stays visible with its reason',
    harvest: 'off',
    get: {
      ...base,
      settings: 'settings_modules_more.read_with_an_unlisted_saved_instance_defaults_preset',
    },
    writes: {
      settingsSave: {
        case: 'settings_modules_more.instance_defaults_saved_without_the_preset_field_keeps_the_unlisted_preset',
        reads: {
          settings:
            'settings_modules_more.read_after_the_partial_instance_save_the_unlisted_preset_remains',
        },
      },
    },
  },
  config_fields_emulators_fallback_off: {
    description: 'C15-2/3 C-03 explicit machine fallback and next tray start off',
    harvest: 'off',
    get: { ...base, settings: 'settings_modules.read_after_saved_instance_defaults' },
    writes: {
      settingsSave: {
        case: 'settings_modules_more.saved_instance_defaults_with_fallback_and_autostart_off',
        reads: {
          settings:
            'settings_modules_more.read_after_saved_instance_defaults_with_fallback_and_autostart_off',
        },
      },
    },
  },
  config_fields_emulators_refused: {
    description: 'C15-1 recorded validation refusal keeps input',
    harvest: 'off',
    get: base,
    // The recording sent a typed-invalid label; the browser cannot produce that type.
    // Reuse its genuine refusal to verify retention after a server-side rejection.
    writes: {
      settingsSave: { mode: 'refused', case: 'settings_modules.invalid_string_instance_defaults' },
    },
  },
  config_fields_emulators_switch: {
    description: 'C15-4 confirmed switch, recorded config verifies new context',
    harvest: 'off',
    get: base,
    writes: {
      settingsSave: {
        case: 'settings_modules.saved_active_instance',
        reads: {
          settings: 'settings_modules.read_after_saved_active_instance',
          config: 'settings_modules.read_config_after_saved_active_instance',
        },
      },
    },
  },
  config_fields_emulators_switch_lost: {
    description: 'C15-4 lost receipt, config still proves the switch',
    harvest: 'off',
    get: base,
    writes: {
      settingsSave: {
        mode: 'drop',
        case: 'settings_modules.saved_active_instance',
        reads: {
          settings: 'settings_modules.read_after_saved_active_instance',
          config: 'settings_modules.read_config_after_saved_active_instance',
        },
      },
    },
  },
  config_fields_emulators_switch_unchanged: {
    description: 'C15-4 lost answer and unchanged config keep Unknown',
    harvest: 'off',
    get: base,
    writes: { settingsSave: { mode: 'drop', case: 'settings_modules.saved_active_instance' } },
  },
  config_fields_emulators_receipt_unverified: {
    description: 'C15-4 successful settings receipt cannot adopt context without config',
    harvest: 'off',
    get: base,
    writes: {
      settingsSave: {
        case: 'settings_modules.saved_active_instance',
        reads: { settings: 'settings_modules.read_after_saved_active_instance' },
      },
    },
  },
};
