// @ts-check
import { DASHBOARD_PAGES_FIXTURE_PATH, loadJsonWithIdentity } from './contract-fixture.mjs';
import { record } from './generate-contract.mjs';

export const HISTORY_FIXTURE_PATH = DASHBOARD_PAGES_FIXTURE_PATH;

export function loadHistoryFixture() {
  return record(record(loadJsonWithIdentity(HISTORY_FIXTURE_PATH).fixture)['cases']);
}
