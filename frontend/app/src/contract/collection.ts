/**
 * The Collection page's contract (collection brief C1 to C6) and the shared settings-module
 * editor's: strict schemas generated from the settings, collection_policies and profile_reads
 * (vocabulary) families of backend/tests/fixtures/dashboard_contract_pages_v1.json, bound to
 * those families' hashes. Each is the answer `{status, body}` of one route.
 */
export {
  collectionPatchAnswerSchema,
  collectionSourceAnswerSchema,
  settingsPostAnswerSchema,
  settingsReadAnswerSchema,
  vocabReadAnswerSchema,
} from './generated/dashboard_contract_pages_v1_collection.ba467b2a.ts';
