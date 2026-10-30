/**
 * F6-15 configuration fields on their owning pages (brief
 * backend/logs/reviews/ui-pages/f6-15-config-fields-brief.md): the schemas of the recorded
 * families settings_modules (the typed modules instance-defaults, active-instance, run-defaults,
 * tournament-cards, loop-defaults, missions-defaults, fleet-defaults), settings_switch_records
 * (the active-instance switch keeps every account record), loadout_restore and
 * loadout_restore_eligibility (restore values of a named loadout) in the pages fixture
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json).
 */
export {
  configFieldsConfigAnswerSchema,
  configFieldsSettingsReadAnswerSchema,
  configFieldsSettingsSaveAnswerSchema,
  restoreConfigAnswerSchema,
  restoreEligibilityConfigAnswerSchema,
  restoreEligibilityPatchAnswerSchema,
  restorePatchAnswerSchema,
  switchConfigAnswerSchema,
  switchSettingsReadAnswerSchema,
  switchSettingsSaveAnswerSchema,
} from './generated/dashboard_contract_pages_v1_config_fields.7f0005d9.ts';
/** r12e (c945da8): settings_modules_more, the F6-15 C-03 recordings. */
export {
  configFieldsMoreConfigAnswerSchema,
  configFieldsMoreSettingsReadAnswerSchema,
  configFieldsMoreSettingsSaveAnswerSchema,
} from './generated/dashboard_contract_pages_v1_config_fields_more.b0ae7b81.ts';
/** r12f: settings_modules_more_2 (restore-only save, profiles without blueprints, malformed lists). */
export {
  configFieldsMore2ConfigAnswerSchema,
  configFieldsMore2LoadoutPatchAnswerSchema,
  configFieldsMore2SettingsReadAnswerSchema,
  configFieldsMore2SettingsSaveAnswerSchema,
  configFieldsMore2SourceAnswerSchema,
} from './generated/dashboard_contract_pages_v1_config_fields_more_2.4e219485.ts';
