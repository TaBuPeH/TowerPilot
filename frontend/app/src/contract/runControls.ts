/**
 * The Runs page's contract (brief backend/logs/reviews/ui-pages/runs-brief.md):
 * strict schemas generated from the run_controls_save and run_controls_live
 * families of backend/tests/fixtures/dashboard_contract_pages_v1.json, bound
 * to those families' hashes (an appended family changes nothing here). Each is
 * the answer `{status, body}` at raw response path `answer`: the POST answers
 * of the two saves, and the GET reads the fixture records to settle them.
 */
export {
  liveControlsAnswerSchema,
  liveControlsReadAnswerSchema,
  runControlsSaveAnswerSchema,
  runControlsSaveReadAnswerSchema,
  type LiveControlsAnswer,
  type LiveControlsReadAnswer,
  type RunControlsSaveAnswer,
  type RunControlsSaveReadAnswer,
} from './generated/dashboard_contract_pages_v1_runs.ff4f699f.ts';
