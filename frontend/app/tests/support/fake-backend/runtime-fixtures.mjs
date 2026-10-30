// @ts-check
import { isDeepStrictEqual } from 'node:util';

/** Recorded predecessor and save transitions, shared by Runtime UI and fake-server tests. */
export const RUNTIME_SAVES = Object.freeze({
  tap: { before: 'settings.populated_account_every_module', after: 'settings.saved_tap' },
  loop: { before: 'settings.saved_device_preview', after: 'settings.saved_loop' },
  missions: { before: 'settings.saved_loop', after: 'settings.saved_missions' },
  fleet: { before: 'settings.saved_missions', after: 'settings.saved_fleet' },
  logging: { before: 'settings.saved_fleet', after: 'settings.saved_logging' },
});

/** Refuse unrecorded Runtime save requests; return only existing recorded refusal cases.
 * @param {Record<string, unknown>} actual
 * @param {Record<string, unknown>} expected
 * @returns {string | null}
 */
export function runtimeSaveRefusal(actual, expected) {
  if (actual['revision'] === undefined || actual['revision'] === null)
    return 'settings.revision_missing';
  if (actual['revision'] !== expected['revision'])
    return 'settings.stale_revision_another_client_saved_first';
  if (!isDeepStrictEqual(actual['context'], expected['context']))
    return 'settings.context_other_emulator';
  return isDeepStrictEqual(actual, expected) ? null : 'settings.unknown_field_for_the_module';
}
