import {
  loadJsonWithIdentity,
  DASHBOARD_PAGES_FIXTURE_PATH,
} from '../../scripts/contract-fixture.mjs';
import { record } from '../../scripts/generate-contract.mjs';
export const previewCases = record(
  record(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture)['cases'],
);
export const previewSample = (family: string, name: string) =>
  record(record(previewCases[family])[name]);
export const previewBody = (family: string, name: string) => previewSample(family, name)['body'];
