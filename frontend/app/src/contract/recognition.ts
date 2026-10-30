/**
 * The Recognition page's contract (screens brief R1 to R8): strict schemas generated from the
 * recognition_reads and calibrate_writes families of
 * backend/tests/fixtures/dashboard_contract_pages_v1.json, bound to those families' hashes.
 * Each is the answer `{status, body}` of one route.
 */
export {
  calibrateApplyAnswerSchema,
  calibrateObserveAnswerSchema,
  calibrateStartAnswerSchema,
  calibrateStopAnswerSchema,
  recognitionConfigAnswerSchema,
  recognitionStatusAnswerSchema,
  recognitionStatusReadbackAnswerSchema,
} from './generated/dashboard_contract_pages_v1_recognition.29aa411f.ts';
