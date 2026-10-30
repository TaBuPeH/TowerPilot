/**
 * F6-12 shopping queue editor (brief backend/logs/reviews/ui-pages/f6-12-shopping-brief.md): the
 * schemas of the recorded shopping_queue family in the pages fixture
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json): saved queue reads and saves
 * (GET/POST /api/run-controls), live queue changes and Try once actions
 * (POST /api/operations/{id}/live-controls and /actions), the battle reads around them, and the
 * shared shopping library (POST /api/profile-patch, GET /api/profile-src/{name}).
 */
export {
  shoppingBattleReadAnswerSchema,
  shoppingBattleWriteAnswerSchema,
  shoppingLibraryReadAnswerSchema,
  shoppingPolicyPatchAnswerSchema,
  shoppingRunReadAnswerSchema,
  shoppingRunSaveAnswerSchema,
} from './generated/dashboard_contract_pages_v1_shopping.84476b5e.ts';
