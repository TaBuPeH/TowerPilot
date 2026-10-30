// @ts-check
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';

/** @param {unknown} value @returns {Record<string, unknown>} */
function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? /** @type {Record<string, unknown>} */ (value)
    : {};
}

/**
 * Profile GETs between sequential recorded writes are absent. Supply their exact receipt
 * profile/revision inside the recorded source envelope; never generate a backend result.
 * `draft` additionally starts a legacy editor from a recorded request's policy, so browser
 * validation/refusal probes need no invented policy values or successful backend answer.
 * @param {string | undefined} reference
 */
export function shoppingLibrarySource(reference) {
  if (reference === undefined || !reference.startsWith('shopping_library_')) return undefined;
  const [mode, name] = reference.split(':');
  const cases = object(object(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture)['cases']);
  const family = object(cases['shopping_queue']);
  const initial = object(family['shopping_library_read_before_any_edit']);
  const selected = object(family[name ?? '']);
  const request = object(selected['request']);
  const body = object(request['body']);
  const envelope = structuredClone(object(initial['body']));
  if (mode === 'shopping_library_after') {
    const receipt = object(selected['body']);
    envelope['profile'] = receipt['profile'];
    envelope['revision'] = receipt['revision'];
  } else if (mode === 'shopping_library_before') {
    const preceding = Object.values(family).find((item) => {
      const answer = object(object(item)['body']);
      return answer['profile'] !== undefined && answer['revision'] === body['expected_revision'];
    });
    if (preceding !== undefined) {
      envelope['profile'] = object(object(preceding)['body'])['profile'];
      envelope['revision'] = body['expected_revision'];
    }
  } else if (mode === 'shopping_library_draft') {
    const profile = object(envelope['profile']);
    const policies = object(profile['policies']);
    const library = object(policies['shopping_lists']);
    const path = body['path'];
    if (!Array.isArray(path) || typeof path[2] !== 'string')
      throw new Error('Missing recorded shopping path');
    library[path[2]] = body['value'];
    envelope['revision'] = body['expected_revision'];
  } else {
    throw new Error(`Unknown shopping source reference ${reference}`);
  }
  return { status: 200, body: envelope };
}
