import { loadFixtureWithIdentity } from '../../../scripts/contract-fixture.mjs';

/** Fail at module load: the loaded fixture must carry the r6 Stop contract (the production one, B-04); no r6 test may skip. */
export function requireR6Fixture(): void {
  const { fixture, identity } = loadFixtureWithIdentity();
  const root: Record<string, unknown> = Object.fromEntries(Object.entries(fixture));
  const stop = root['stop_operation'];
  function record(value: unknown): Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? Object.fromEntries(Object.entries(value))
      : {};
  }
  const families = record(stop);
  const cancel = Object.values(record(families['cancel']));
  if (
    Object.keys(record(families['conflicts'])).length === 0 ||
    cancel.length === 0 ||
    cancel.some(
      (sample) => !Object.hasOwn(record(record(record(sample)['body'])['stop']), 'by_this_request'),
    )
  ) {
    throw new Error(
      `R6 fixture required: ${identity.path} (${identity.sha256}) lacks stop_operation.conflicts or stop_operation.cancel.*.body.stop.by_this_request; no r6 tests may skip.`,
    );
  }
}
