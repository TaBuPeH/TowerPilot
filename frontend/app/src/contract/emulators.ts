/**
 * The Emulators page's contract (emulators brief M1 to M8): strict schemas generated from
 * the emulator_actions, emulator_inventory and emulator_tools families of
 * backend/tests/fixtures/dashboard_contract_pages_v1.json, bound to those families' hashes.
 * Each is the answer `{status, body}` of one route.
 */
export {
  emulatorsAdoptAnswerSchema,
  emulatorsConfigAnswerSchema,
  emulatorsInstallAnswerSchema,
  emulatorsInventoryAnswerSchema,
  emulatorsLaunchAnswerSchema,
  emulatorsPrepareAnswerSchema,
  emulatorsResolutionAnswerSchema,
  emulatorsToolsAnswerSchema,
  emulatorsWizardStateAnswerSchema,
} from './generated/dashboard_contract_pages_v1_emulators.b9f7a6e2.ts';
