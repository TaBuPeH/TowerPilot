// @ts-check
/** Select recorded preview requests; explicitly identify unsupported viewer combinations. */
import { record } from '../../../scripts/generate-contract.mjs';

/** @typedef {{ stream: string, status: string, requestMode: 'recorded'|'synthetic-request-plumbing', reason: string|null, requested: string, recorded: string }} PreviewBinding */
/** @type {Readonly<Record<string,string>>} */
const STATUS_FOR_STREAM = {
  balanced: 'status_after_balanced',
  low: 'status_after_low',
  fractional_fps: 'status_after_fractional_fps',
  start_auto: 'streaming_mumu',
  start_mumu: 'streaming_mumu',
  start_scrcpy: 'streaming_scrcpy',
  start_adb: 'streaming_adb',
  start_while_automation_owns_screen: 'streaming_mumu',
  fallback_mumu_to_scrcpy: 'fallback_mumu_to_scrcpy',
  fallback_mumu_scrcpy_to_adb: 'fallback_mumu_scrcpy_to_adb',
  fallback_scrcpy_to_adb: 'fallback_scrcpy_to_adb',
  requested_mumu_falls_back_to_scrcpy: 'requested_mumu_falls_back_to_scrcpy',
  error_marker_then_recovery: 'error_marker_status_before_recovery',
};
/** The stream ID alone is rebound to a fresh browser session; every other query must match.
 * @param {URL} requested @param {URL} recorded */
function matches(requested, recorded) {
  const entries = (/** @type {URL} */ url) =>
    [...url.searchParams.entries()]
      .filter(([key]) => key !== 'stream_id')
      .sort(([a], [b]) => a.localeCompare(b));
  return (
    requested.pathname === recorded.pathname &&
    JSON.stringify(entries(requested)) === JSON.stringify(entries(recorded))
  );
}
/** @param {unknown} cases @param {URL} requested @param {import('./preview-fixtures.mjs').PreviewScenario} scenario
 * @returns {PreviewBinding} */
export function selectPreviewReplay(cases, requested, scenario) {
  const streams = record(record(cases)['preview_stream']);
  const pathOf = (/** @type {string} */ name) => {
    const path = record(record(streams[name])['request'])['path'];
    if (typeof path !== 'string') throw new Error('Recorded stream request missing');
    return path;
  };
  const source = requested.searchParams.get('backend') ?? 'auto';
  const exact = [
    'balanced',
    'low',
    'fractional_fps',
    'start_auto',
    'start_mumu',
    'start_scrcpy',
    'start_adb',
  ].find((name) => matches(requested, new URL(pathOf(name), 'http://fixture')));
  // Unsupported combinations exercise URL/preference plumbing only. Keep the requested source's
  // recorded parts; never let a quality choice silently substitute a different source's pixels.
  const plumbing =
    source !== 'auto'
      ? `start_${source}`
      : requested.searchParams.get('q') === '70'
        ? 'low'
        : requested.searchParams.get('q') === '85'
          ? 'balanced'
          : 'start_auto';
  const stream = scenario.stream ?? exact ?? plumbing;
  const recorded = pathOf(stream);
  const exactRequest = matches(requested, new URL(recorded, 'http://fixture'));
  return {
    stream,
    status: scenario.status ?? STATUS_FOR_STREAM[stream] ?? 'missing_session',
    requestMode: exactRequest ? 'recorded' : 'synthetic-request-plumbing',
    reason: exactRequest
      ? null
      : 'No exact recording for these viewer parameters (or an explicit fault scenario). Reuse named recorded parts for request plumbing only; this is not an observed backend answer to that combination.',
    requested: requested.pathname + requested.search,
    recorded,
  };
}
/** @returns {PreviewFixtureState} */
export function createPreviewFixtureState() {
  return { streams: new Map(), active: new Set(), maxActive: 0 };
}
/** @typedef {{ streams: Map<string,PreviewBinding>, active: Set<string>, maxActive: number }} PreviewFixtureState */
