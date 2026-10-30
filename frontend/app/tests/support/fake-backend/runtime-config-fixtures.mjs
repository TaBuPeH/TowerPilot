// @ts-check
/** Exact recorded predecessor, save and readback for each machine fallback. */
export const RUNTIME_CONFIG_SAVES = Object.freeze({
  'run-defaults': { before: 'read_after_saved_instance_defaults', suffix: 'run_defaults' },
  'loop-defaults': { before: 'read_after_saved_tournament_cards', suffix: 'loop_defaults' },
  'missions-defaults': { before: 'read_after_saved_loop_defaults', suffix: 'missions_defaults' },
  'fleet-defaults': { before: 'read_after_saved_missions_defaults', suffix: 'fleet_defaults' },
});
export const RUNTIME_CONFIG_CONTEXT =
  'settings_switch_records.read_config_after_switching_back_every_record_as_it_was';

/** @type {Readonly<Record<string, import('./scenarios.mjs').Scenario>>} */
export const RUNTIME_CONFIG_SCENARIOS = {
  ...Object.fromEntries(
    [
      ['unlisted', 'read_with_an_unlisted_saved_run_defaults_preset'],
      ['no_runnable', 'read_with_no_runnable_run_defaults_preset_options'],
      ['no_presets', 'read_with_no_presets_in_the_configuration_the_options_are_empty'],
      ['placeholder', 'read_with_a_placeholder_preset_in_the_configuration'],
    ].map(([mode, read]) => [
      `config_fields_runtime_run_defaults_${mode}`,
      {
        description: `C15-5 C-03 recorded ${mode} preset choices`,
        harvest: 'off',
        get: { config: RUNTIME_CONFIG_CONTEXT, settings: `settings_modules_more.${read}` },
        ...(mode === 'unlisted'
          ? {
              writes: {
                settingsSave: {
                  case: 'settings_modules_more.run_defaults_choosing_a_listed_preset_replaces_the_unlisted_one',
                  reads: {
                    settings:
                      'settings_modules_more.read_after_the_unlisted_run_preset_was_replaced',
                  },
                },
              },
            }
          : {}),
      },
    ]),
  ),
  ...Object.fromEntries(
    Object.entries(RUNTIME_CONFIG_SAVES).flatMap(([module, transition]) =>
      ['saved', 'stale', 'unknown', 'override'].map((mode) => [
        `config_fields_runtime_${transition.suffix}_${mode}`,
        {
          description: `Recorded ${module} ${mode}; settings only, no automation.`,
          harvest: 'off',
          get: {
            config: RUNTIME_CONFIG_CONTEXT,
            settings:
              mode === 'override'
                ? `settings_modules_more.read_before_saved_${transition.suffix}_with_overrides`
                : `settings_modules.${transition.before}`,
          },
          writes: {
            settingsSave:
              mode === 'unknown'
                ? { mode: 'drop' }
                : {
                    case:
                      mode === 'override'
                        ? `settings_modules_more.saved_${transition.suffix}_with_overrides`
                        : `settings_modules.${mode === 'saved' ? 'saved' : 'stale_revision'}_${transition.suffix}`,
                    ...(mode === 'saved' || mode === 'override'
                      ? {
                          reads: {
                            settings:
                              mode === 'override'
                                ? `settings_modules_more.read_after_saved_${transition.suffix}_with_overrides`
                                : `settings_modules.read_after_saved_${transition.suffix}`,
                          },
                        }
                      : {}),
                  },
          },
        },
      ]),
    ),
  ),
};
