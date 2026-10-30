// @ts-check
/**
 * The fixture-based fake backend: what `npm run dev` and the Playwright suite
 * talk to instead of a real dashboard. Reads and writes use fixture cases only;
 * every POST is recorded locally and there is no forwarding facility.
 *
 *   node tests/support/fake-backend/fake-backend.mjs [--port 4174] [--scenario collecting]
 *
 * serves the built app (frontend/webui/app) at /ui/app/ and the fixture API at /api/.
 */
import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { APP } from '../paths.ts';
import { pathToFileURL } from 'node:url';
import { crc32, deflateSync } from 'node:zlib';
import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  FIXTURE_PATH,
  loadFixture,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import { DEFAULT_GETS, SCENARIOS } from './scenarios.mjs';
import { servePreview } from './preview-fixtures.mjs';
import { createPreviewFixtureState } from './preview-replay.mjs';
import { RUNTIME_SAVES, runtimeSaveRefusal } from './runtime-fixtures.mjs';
import { actionsCase, historyAnswer } from '../history-fixtures.mjs';
import { syntheticAnswer } from '../synthetic-action-fixtures.mjs';
import { shoppingLibrarySource } from '../shopping-library-fixtures.mjs';
import { profilePatchFencedRead } from './profile-patch-fenced-fixtures.mjs';
import {
  ABILITIES_FIXTURE_PATH,
  abilitiesRead,
  abilitiesWrite,
  abilitiesSession,
} from '../abilities-fixtures.mjs';
import { scheduleRead, scheduleSession, scheduleWrite } from '../schedule-fixtures.mjs';
import {
  EQUIPMENT_FIXTURE_PATH,
  equipmentRead,
  equipmentSession,
  equipmentWrite,
} from '../equipment-fixtures.mjs';

export { FIXTURE_PATH, loadFixture };

export const BUILD_DIR = path.resolve(APP, '../webui/app');
export const DEFAULT_SCENARIO = 'collecting';

const CONTENT_TYPES = /** @type {Record<string, string>} */ ({
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
});

/**
 * @typedef {import('../../../scripts/contract-fixture.mjs').HarvestFixture} HarvestFixture
 * @typedef {import('node:http').IncomingMessage & { originalUrl?: string | undefined }} Request
 * @typedef {import('node:http').ServerResponse} Response
 * @typedef {import('./scenarios.mjs').Scenario} Scenario
 * @typedef {import('./scenarios.mjs').WriteMode} WriteMode
 * @typedef {import('./scenarios.mjs').WriteChoice} WriteChoice
 * @typedef {{ status: number, body: Record<string, unknown> }} FixtureCase
 * @typedef {{ method: string, path: string, body: string, time: number }} RecordedPost
 * @typedef {{ posts: RecordedPost[], diagnostics: RecordedPost[], harvest?: string, history?: string, preview?: import('./preview-replay.mjs').PreviewFixtureState, schedule?: import('../schedule-fixtures.mjs').ScheduleSession, equipment?: import('../equipment-fixtures.mjs').EquipmentSession, abilities?: import('../abilities-fixtures.mjs').AbilitiesSession, reads?: Record<string, string> }} Session
 * @typedef {{
 *   scenario?: string,
 *   scenarios?: Readonly<Record<string, Scenario>>,
 *   dashboardFixture?: unknown,
 *   pagesFixture?: unknown,
 *   target?: string,
 *   realBackendTarget?: string,
 *   env?: NodeJS.ProcessEnv
 * }} HandlerOptions
 */

/**
 * @param {Response} res
 * @param {number} status
 * @param {unknown} body
 */
function sendJson(res, status, body) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

/** Fail before loading fixtures or opening a listener. @param {HandlerOptions} options */
function refuseRealTarget(options) {
  const env = options.env ?? process.env;
  if (
    [options.target, options.realBackendTarget, env['TOWER_PILOT_DEV_BACKEND']].some(
      (target) => target !== undefined && target.trim() !== '',
    )
  ) {
    throw new Error(
      'The fake backend refuses real-backend targets; it serves fixtures and never forwards.',
    );
  }
}

/** @param {unknown} value @returns {Record<string, unknown>} */
function record(value) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Expected a fixture object');
  }
  return /** @type {Record<string, unknown>} */ (value);
}

/** @param {unknown} root @param {string} reference @returns {unknown} */
function lookup(root, reference) {
  let value = root;
  for (const key of reference.split('.')) {
    if (typeof value !== 'object' || value === null || !Object.hasOwn(value, key)) return undefined;
    value = Reflect.get(value, key);
  }
  return value;
}

/** @param {unknown} root @param {string} reference @returns {FixtureCase} */
function fixtureCase(root, reference) {
  const value = record(lookup(root, reference));
  if (typeof value['status'] !== 'number') throw new Error(`No fixture case ${reference}`);
  return { status: value['status'], body: structuredClone(record(value['body'])) };
}

/** Fixture HTML bodies are sent as text, exactly as Flask would send them.
 * @param {Response} res @param {FixtureCase} answer
 */
function sendCase(res, answer) {
  if (answer.body['binary'] !== undefined) {
    const binary = record(answer.body['binary']);
    const width = binary['width'];
    const height = binary['height'];
    const contentType = binary['content_type'];
    const cacheControl = binary['cache_control'] ?? 'no-store';
    if (
      !Number.isInteger(width) ||
      !Number.isInteger(height) ||
      typeof width !== 'number' ||
      typeof height !== 'number' ||
      width <= 0 ||
      height <= 0
    )
      throw new Error('Recorded image has invalid dimensions');
    res.statusCode = answer.status;
    if (typeof contentType !== 'string' || typeof cacheControl !== 'string')
      throw new Error('Recorded image has invalid headers');
    res.setHeader('Content-Type', contentType);
    res.setHeader('Cache-Control', cacheControl);
    res.end(syntheticPng(width, height));
  } else if (typeof answer.body['text'] === 'string' && Object.keys(answer.body).length === 1) {
    res.statusCode = answer.status;
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.end(answer.body['text']);
  } else {
    sendJson(res, answer.status, answer.body);
  }
}

/**
 * Recorded pixels are a hash, not bytes. These synthetic grayscale PNGs preserve
 * the recorded dimensions and contain no captured game images.
 * @param {number} width @param {number} height
 */
function syntheticPng(width, height) {
  /** @param {string} type @param {Buffer} data */
  const chunk = (type, data) => {
    const payload = Buffer.concat([Buffer.from(type), data]);
    const size = Buffer.alloc(4);
    size.writeUInt32BE(data.length);
    const checksum = Buffer.alloc(4);
    checksum.writeUInt32BE(crc32(payload));
    return Buffer.concat([size, payload, checksum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // Eight-bit grayscale; the remaining IHDR fields are zero.
  const pixels = Buffer.alloc((width + 1) * height, 96);
  for (let row = 0; row < height; row++) pixels[row * (width + 1)] = 0;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(pixels)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** @param {Response} res @param {number} status */
function sendFlaskError(res, status) {
  sendCase(res, {
    status,
    body: {
      text: `<!doctype html>\n<html lang=en>\n<title>${status} ${status === 502 ? 'Bad Gateway' : 'Internal Server Error'}</title>\n<h1>${status === 502 ? 'Bad Gateway' : 'Internal Server Error'}</h1>\n`,
    },
  });
}

/** @type {Readonly<Record<string, string>>} */
const GET_ROUTES = Object.freeze({
  '/api/status': 'status',
  '/api/calibrate/status': 'calibrate',
  '/api/wizard/templates': 'templates',
  '/api/wizard/required': 'required',
  '/api/catalogue/modules': 'modules',
  '/api/wizard/scan-plan': 'scanPlan',
  '/api/screen-analysis/status': 'analysisStatus',
  '/api/screen-analysis/frame.png': 'analysisFrame',
  '/api/frame.png': 'frame',
  '/api/battles/active': 'battlesActive',
  '/api/schedule/status': 'schedule',
  '/api/battles': 'battles',
  '/api/config': 'config',
  '/api/accounts': 'accounts',
  '/api/runs': 'runs',
  '/api/wizard/tools': 'wizardTools',
  '/api/wizard/state': 'wizardState',
  '/api/wizard/emulators': 'wizardEmulators',
  '/api/wizard/resolution': 'resolution',
  '/api/run-controls': 'runControls',
  '/api/readiness': 'readiness',
  '/api/harvest': 'harvest',
  '/api/profile-files': 'profileFiles',
  '/api/settings': 'settings',
  '/api/run-templates': 'runTemplates',
  '/api/vocab': 'vocab',
  '/api/runtime': 'runtime',
  '/api/diagnostics/events': 'runtimeEvents',
});

/** Matching answer for each request body; references resolve Dashboard first, then harvest.
 * Diagnostics have no recorded 5xx case: unknown mode uses a Flask-like 500 page.
 * @type {Readonly<Record<string, readonly [string, string, string | null]>>}
 */
const WRITE_CASES = Object.freeze({
  runTemplatesAdd: [
    'run_templates_add_fenced.added_with_the_matching_revision_and_context',
    'run_templates_add_fenced.write_failed_nothing_saved',
    'run_templates_add_fenced.written_then_the_answer_failed',
  ],
  calibrateStart: [
    'calibrate_writes.start_bootstrap_with_the_action_capture_flows',
    'calibrate_writes.start_taps_off_409',
    null,
  ],
  calibrateObserve: [
    'calibrate_writes.observe_watch_30_seconds',
    'calibrate_writes.observe_while_a_calibration_is_running_409',
    null,
  ],
  calibrateApply: [
    'calibrate_writes.apply_awaiting_apply_is_applied',
    'calibrate_writes.apply_stale_revision_409',
    'calibrate_writes.apply_unexpected_failure_500',
  ],
  calibrateStop: [
    'calibrate_writes.stop_running_calibration_asks_the_worker_to_stop',
    'calibrate_writes.stop_stale_revision_409',
    null,
  ],
  analysisStart: [
    'screen_analysis.start_accepted',
    'screen_analysis.start_while_an_analysis_is_running_is_refused',
    'screen_analysis.start_when_the_worker_cannot_be_started_is_a_text_500',
  ],
  templateWrite: [
    'recognition_images.save_a_crop_of_the_cached_frame',
    'recognition_images.save_over_an_existing_template_without_the_overwrite_flag_409',
    'recognition_images.save_when_the_png_cannot_be_written_500',
  ],
  'harvest:false': [
    'post_harvest.off_ok_stopped',
    'refusals.device_owned_process',
    'post_harvest.outcome_unknown',
  ],
  'harvest:true': [
    'post_harvest.enable_ok',
    'refusals.device_owned_process',
    'post_harvest.outcome_unknown',
  ],
  'automationAccess:false': [
    'automation_access.disable_input_stopped',
    'automation_access.disable_driver_input_refused',
    'automation_access.disable_marker_write_failed',
  ],
  'automationAccess:true': [
    'automation_access.enable_ok',
    'automation_access.enable_refused_owner',
    'write_unknown_outcomes.allow_input_cleared_the_revocation_then_the_save_raised',
  ],
  cancel: [
    'stop_operation.cancel.stopped',
    'refusals.device_owned_process',
    'write_unknown_outcomes.stop_paused_harvesting_then_the_cancel_raised',
  ],
  'control:start': [
    'control.start_accepted',
    'refusals.device_owned_process',
    'control_unknown_outcomes.start_accepted_then_the_process_refresh_raised',
  ],
  'control:start_gems': [
    'control.start_gems_accepted',
    'refusals.device_owned_process',
    'control_unknown_outcomes.start_gems_accepted_then_the_process_refresh_raised',
  ],
  'control:kill': [
    'stop_operation.kill.stopped',
    'stop_operation.kill.created_missing',
    'control_unknown_outcomes.kill_access_denied',
  ],
  'control:clear_flag': [
    'control.clear_flag_accepted',
    'control.action_lock',
    'control_unknown_outcomes.clear_flag_removed_then_the_answer_failed',
  ],
  handoff: [
    'handoff.recorded',
    'handoff.refused_device_owned',
    'write_unknown_outcomes.handoff_recorded_then_the_answer_failed',
  ],
  reconnect: [
    'reconnect.accepted_device_state_device',
    'reconnect.refused_while_a_runner_is_listed',
    'reconnect.adb_connect_raised',
  ],
  adopt: [
    'emulator_actions.adopt_a_serial_saved_and_connected',
    'emulator_actions.adopt_the_instance_changed_since_the_scan',
    'emulator_actions.adopt_the_config_could_not_be_written_500',
  ],
  launch: [
    'emulator_actions.launch_a_vm_no_instance_uses_skips_the_boot_pipeline',
    'emulator_actions.launch_refused_while_a_runner_owns_the_device',
    'emulator_actions.launch_the_manager_could_not_be_started_500',
  ],
  prepare: [
    'emulator_actions.prepare_changes_the_adb_switch_and_the_framebuffer',
    'emulator_actions.prepare_refused_while_bluestacks_is_running',
    null,
  ],
  toolsInstall: [
    'emulator_tools.install_accepted_202_the_download_starts',
    'emulator_tools.install_refused_while_a_runner_owns_the_device',
    null,
  ],
  actions: [
    'battle_actions.accepted_command_requested',
    'battle_actions.replaced_battle_refused',
    'battle_actions.admitted_then_the_readback_failed_unknown',
  ],
  // Runs page (brief RUN-05 to RUN-10): the pages fixture's saved-run save and live change.
  runControlsSave: [
    'run_controls_save.saved_switch_off',
    'run_controls_save.stale_revision_another_client_saved_first',
    'run_controls_save.saved_then_the_answer_failed_500',
  ],
  settingsSave: [
    'settings.saved_rewards',
    'settings.stale_revision_another_client_saved_first',
    'settings.saved_then_the_answer_failed_500',
  ],
  loadoutPatch: [
    'loadout_restore.saved_cards_restore',
    'loadout_restore.refused_cards_restore',
    null,
  ],
  profilePatch: [
    'profile_patch.multi_save_first_request_saved',
    'profile_patch.stale_revision_409',
    'profile_patch.written_then_the_answer_failed_500',
  ],
  liveControls: [
    'run_controls_live.accepted_switch_off',
    'run_controls_live.stale_control_revision_refused',
    'run_controls_live.admitted_then_the_readback_failed_unknown',
  ],
  // Profiles page (profiles brief P1 to P5): the pages fixture's account and profile writes.
  'accountsWrite:select': [
    'accounts_write.select_an_existing_account',
    'accounts_write.refused_while_a_runner_owns_the_device',
    null,
  ],
  'accountsWrite:create': [
    'accounts_write.create_a_fresh_account',
    'accounts_write.create_a_duplicate_id',
    null,
  ],
  accountTier: [
    'accounts_write.tier_saved_by_the_operator',
    'accounts_write.tier_21',
    'accounts_write.tier_write_failed_500',
  ],
  profileActivate: [
    'profile_files_write.activate_another_profile',
    'profile_files_write.activate_another_accounts_profile_is_refused',
    'profile_files_write.activate_write_failed_500',
  ],
  profileCopy: [
    'profile_files_write.copy_one_of_your_own_profiles',
    'profile_files_write.copy_onto_an_existing_profile_is_refused',
    null,
  ],
  profilePromote: [
    'profile_files_write.promotion_returns_the_problems_it_does_not_enforce_them',
    'profile_files_write.promote_onto_an_existing_profile_is_refused',
    null,
  ],
  diagnostics: ['diagnostics_client.accepted', 'diagnostics_client.refused_unsupported_kind', null],
});

/** @param {string} pathname @param {Record<string, unknown>} body */
function writeRoute(pathname, body) {
  if (pathname.startsWith('/api/template/')) return { id: 'templateWrite', key: 'templateWrite' };
  if (pathname === '/api/harvest')
    return { id: 'harvest', key: `harvest:${String(body['enabled'])}` };
  if (pathname === '/api/automation-access')
    return { id: 'automationAccess', key: `automationAccess:${String(body['enabled'])}` };
  if (pathname === '/api/control')
    return { id: 'control', key: `control:${String(body['action'])}` };
  if (/^\/api\/operations\/[^/]+\/cancel$/.test(pathname)) return { id: 'cancel', key: 'cancel' };
  if (/^\/api\/operations\/[^/]+\/actions$/.test(pathname))
    return { id: 'actions', key: `actions:${String(body['action'])}` };
  if (/^\/api\/operations\/[^/]+\/live-controls$/.test(pathname))
    return { id: 'liveControls', key: 'liveControls' };
  if (pathname === '/api/run-templates/add')
    return { id: 'runTemplatesAdd', key: 'runTemplatesAdd' };
  if (pathname === '/api/run-controls') return { id: 'runControlsSave', key: 'runControlsSave' };
  if (pathname === '/api/settings') return { id: 'settingsSave', key: 'settingsSave' };
  if (pathname === '/api/profile-patch') return { id: 'profilePatch', key: 'profilePatch' };
  if (pathname === '/api/loadout-patch') return { id: 'loadoutPatch', key: 'loadoutPatch' };
  if (pathname === '/api/accounts')
    return {
      id: 'accountsWrite',
      key: `accountsWrite:${body['create'] === true ? 'create' : 'select'}`,
    };
  if (pathname === '/api/account-tier') return { id: 'accountTier', key: 'accountTier' };
  if (pathname === '/api/profile-activate')
    return { id: 'profileActivate', key: 'profileActivate' };
  if (pathname === '/api/profile-copy') return { id: 'profileCopy', key: 'profileCopy' };
  if (pathname === '/api/profile-promote') return { id: 'profilePromote', key: 'profilePromote' };
  const id = /** @type {Record<string, string>} */ ({
    '/api/calibrate/start': 'calibrateStart',
    '/api/calibrate/observe': 'calibrateObserve',
    '/api/calibrate/apply': 'calibrateApply',
    '/api/calibrate/stop': 'calibrateStop',
    '/api/screen-analysis/start': 'analysisStart',
    '/api/setup-battle/collection-handoff': 'handoff',
    '/api/wizard/reconnect': 'reconnect',
    '/api/wizard/adopt': 'adopt',
    '/api/wizard/launch': 'launch',
    '/api/wizard/bluestacks/prepare': 'prepare',
    '/api/wizard/tools/install': 'toolsInstall',
    '/api/diagnostics/client': 'diagnostics',
  })[pathname];
  return id === undefined ? null : { id, key: id };
}

/** Cookie values are opaque: malformed values cannot select a scenario or session.
 * @param {Request} req @param {string} name
 */
function cookie(req, name) {
  for (const part of (req.headers.cookie ?? '').split(';')) {
    const [key, ...value] = part.trim().split('=');
    if (key === name) return value.join('=');
  }
  return undefined;
}

/**
 * Fixture-only middleware for Node and Vite. One handler owns its session logs;
 * browser contexts use tp_fake_scenario and tp_fake_session (Path=/).
 * Control routes are GET only. `?scenario=` remains available for old smoke links.
 * @param {HarvestFixture} fixture
 * @param {HandlerOptions} [options]
 * @returns {(req: Request, res: Response) => void}
 */
export function createApiHandler(fixture, options = {}) {
  refuseRealTarget(options);
  const scenarios = options.scenarios ?? SCENARIOS;
  const fallbackScenario = options.scenario ?? DEFAULT_SCENARIO;
  if (!Object.hasOwn(scenarios, fallbackScenario))
    throw new Error(`No fixture scenario named ${fallbackScenario}`);
  const dashboard = record(
    options.dashboardFixture ?? loadJsonWithIdentity(DASHBOARD_FIXTURE_PATH).fixture,
  )['cases'];
  const pages = record(
    options.pagesFixture ?? loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture,
  )['cases'];
  const equipment = record(loadJsonWithIdentity(EQUIPMENT_FIXTURE_PATH).fixture)['cases'];
  // Supplemental ability worlds are large; unrelated fake scenarios never need to parse them.
  /** @type {unknown} */
  let abilities;
  const abilityCases = () => {
    abilities ??= record(loadJsonWithIdentity(ABILITIES_FIXTURE_PATH).fixture)['cases'];
    return abilities;
  };
  /** @type {Map<string, Session>} */
  const sessions = new Map();
  /** PE6: no intermediate GET was recorded between the sequential target saves.
   * Reuse the exact preceding recorded receipt's profile/revision in the genuine GET
   * envelope. These are explicitly modified cases, not new backend recordings.
   * @param {string} reference
   */
  const runsTargetSource = (reference) => {
    const preceding =
      reference === 'profile_target_runs_before_label'
        ? 'inactive_rescue_policy_saved'
        : reference === 'profile_target_runs_after_label'
          ? 'inactive_run_entry_label_saved'
          : null;
    if (preceding === null) return undefined;
    const source = fixtureCase(pages, 'profile_target.same_account_inactive_target_source');
    const receipt = fixtureCase(pages, `profile_target.${preceding}`);
    source.body['profile'] = receipt.body['profile'];
    source.body['revision'] = receipt.body['revision'];
    return source;
  };
  /** A reference names a Dashboard case, then a pages case, then a harvest one. @param {string} reference */
  const answerFor = (reference) =>
    (reference.startsWith('b1:') ? syntheticAnswer(reference) : undefined) ??
    // A2-7: `r17b:<family>.<case>` names a landed r17b recording (backend/tests/fixtures/dashboard_contract_pages_v1.json).
    (reference.startsWith('r17b:') ? actionsCase(reference.slice('r17b:'.length)) : undefined) ??
    runsTargetSource(reference) ??
    fixtureCase(
      lookup(dashboard, reference) !== undefined
        ? dashboard
        : lookup(pages, reference) !== undefined
          ? pages
          : reference.startsWith('abilities_samples.')
            ? abilityCases()
            : lookup(equipment, reference) !== undefined
              ? equipment
              : fixture,
      reference,
    );

  return (req, res) => {
    const url = new URL(req.originalUrl ?? req.url ?? '/', 'http://fake-backend');
    const control = url.pathname.startsWith('/__fake/');
    if (control ? req.method !== 'GET' : !['GET', 'HEAD', 'POST'].includes(req.method ?? '')) {
      res.setHeader('Allow', control ? 'GET' : 'GET, HEAD, POST');
      sendJson(res, 405, { ok: false, error: 'Unsupported fake-backend method' });
      return;
    }
    let sessionId = cookie(req, 'tp_fake_session');
    if (sessionId === undefined || !sessions.has(sessionId)) {
      sessionId = randomUUID();
      sessions.set(sessionId, { posts: [], diagnostics: [] });
      res.setHeader('Set-Cookie', [`tp_fake_session=${sessionId}; Path=/; HttpOnly; SameSite=Lax`]);
    }
    const session = sessions.get(sessionId);
    if (session === undefined) throw new Error('Fake session missing');
    const name =
      url.searchParams.get('scenario') ?? cookie(req, 'tp_fake_scenario') ?? fallbackScenario;
    const scenario = Object.hasOwn(scenarios, name) ? scenarios[name] : undefined;
    if (scenario === undefined) {
      sendJson(res, 404, { ok: false, error: `No fixture scenario named ${name}` });
      return;
    }
    if (control) {
      if (url.pathname === '/__fake/use') {
        // Switching states starts a clean session and cannot inherit a prior write's follow-up.
        const id = randomUUID();
        sessions.set(id, { posts: [], diagnostics: [] });
        res.setHeader('Set-Cookie', [
          `tp_fake_scenario=${name}; Path=/; SameSite=Lax`,
          `tp_fake_session=${id}; Path=/; HttpOnly; SameSite=Lax`,
        ]);
        res.statusCode = 302;
        res.setHeader('Location', '/ui/app/');
        res.setHeader('Cache-Control', 'no-store');
        res.end();
      } else if (url.pathname === '/__fake/preview') {
        sendJson(res, 200, {
          bindings: [...(session.preview?.streams.values() ?? [])],
          activeCount: session.preview?.active.size ?? 0,
          maxActive: session.preview?.maxActive ?? 0,
        });
      } else if (url.pathname === '/__fake/set') {
        // A warm change of state (spec 9.1 X3, X10): the next reads answer from the named
        // scenario, in the same session, with no navigation and no reset of recorded writes.
        res.setHeader('Set-Cookie', [`tp_fake_scenario=${name}; Path=/; SameSite=Lax`]);
        sendJson(res, 200, { ok: true, scenario: name });
      } else if (url.pathname === '/__fake/history') {
        session.history = url.searchParams.get('case') ?? '';
        sendJson(res, 200, { ok: true });
      } else if (url.pathname === '/__fake/schedule') {
        session.schedule = scheduleSession(url);
        sendJson(res, 200, { ok: true });
      } else if (url.pathname === '/__fake/equipment') {
        session.equipment = equipmentSession(url);
        sendJson(res, 200, { ok: true });
      } else if (url.pathname === '/__fake/abilities') {
        session.abilities = abilitiesSession(url);
        sendJson(res, 200, { ok: true });
      } else if (url.pathname === '/__fake/posts') {
        sendJson(res, 200, { posts: session.posts, diagnostics: session.diagnostics });
      } else if (url.pathname === '/__fake/reset') {
        session.posts.length = 0;
        session.diagnostics.length = 0;
        delete session.harvest;
        delete session.reads;
        delete session.schedule;
        delete session.equipment;
        delete session.abilities;
        sendJson(res, 200, { ok: true });
      } else {
        sendJson(res, 404, { ok: false, error: 'Unknown fake control path' });
      }
      return;
    }
    if (req.method === 'POST') {
      const chunks = /** @type {Buffer[]} */ ([]);
      req.on('data', (/** @type {Buffer<ArrayBufferLike>} */ chunk) => {
        chunks.push(chunk);
      });
      req.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        const entry = {
          method: 'POST',
          path: url.pathname + url.search,
          body: text,
          time: Date.now(),
        };
        (url.pathname === '/api/diagnostics/client' ? session.diagnostics : session.posts).push(
          entry,
        );
        if (writeRoute(url.pathname, {}) === null) {
          sendJson(res, 404, { ok: false, error: 'Unknown fake write path' });
          return;
        }
        /** @type {Record<string, unknown>} */
        let body;
        try {
          body = record(JSON.parse(text));
        } catch {
          sendJson(res, 400, { ok: false, error: 'Expected a JSON object' });
          return;
        }
        if (url.pathname === '/api/settings' && scenario.runtimeSave !== undefined) {
          const transition = RUNTIME_SAVES[scenario.runtimeSave];
          const expected = record(lookup(pages, `${transition.after}.request.body`));
          const refusal = runtimeSaveRefusal(body, expected);
          if (refusal === null) session.reads = { ...session.reads, settings: transition.after };
          sendCase(res, answerFor(refusal ?? transition.after));
          return;
        }
        const scheduled =
          abilitiesWrite(url.pathname, session.abilities) ??
          equipmentWrite(url.pathname, session.equipment) ??
          scheduleWrite(url.pathname, session.schedule);
        if (scheduled !== undefined) {
          const reply = () => {
            if (scheduled.mode === 'hold') return;
            if (scheduled.mode === 'drop') {
              res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '64' });
              res.write('{', () => {
                req.socket.destroy();
              });
            } else sendCase(res, answerFor(scheduled.reference));
          };
          if (scheduled.delay > 0) setTimeout(reply, scheduled.delay);
          else reply();
          return;
        }
        const route = writeRoute(url.pathname, body);
        const cases =
          route === null ? undefined : (WRITE_CASES[route.key] ?? WRITE_CASES[route.id]);
        if (route === null || cases === undefined) {
          sendJson(res, 404, { ok: false, error: 'Unknown fake write path or action' });
          return;
        }
        const entryPath = body['path'];
        const entryChoice =
          name.startsWith('profile_target_runs_') &&
          route.id === 'profilePatch' &&
          Array.isArray(entryPath)
            ? scenario.writes?.[`profilePatch:${String(entryPath.at(-1))}`]
            : undefined;
        const selected = entryChoice ?? scenario.writes?.[route.key] ?? scenario.writes?.[route.id];
        /** @type {WriteChoice} */
        const choice = typeof selected === 'string' ? { mode: selected } : (selected ?? {});
        const mode = choice.mode ?? scenario.writeMode ?? 'ok';
        if (mode === 'hold') return;
        // What the server's reads say after it acted (a save the read then shows): this session only.
        if (choice.reads !== undefined) session.reads = { ...session.reads, ...choice.reads };
        if (mode === 'drop') {
          // The answer is lost after the server acted: a status line and part of the body, then
          // the socket closes. Sending some bytes matters: a socket closed with no bytes at all
          // makes Chromium silently resend the POST on a reused connection (ERR_EMPTY_RESPONSE),
          // which would hide whether the app itself ever resends (S-010).
          res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '64' });
          res.write('{', () => {
            req.socket.destroy();
          });
          return;
        }
        const reference = choice.case ?? cases[mode === 'ok' ? 0 : mode === 'refused' ? 1 : 2];
        if (reference === null) {
          sendFlaskError(res, 500);
          return;
        }
        const answer = answerFor(reference);
        if (
          answer.status < 300 &&
          answer.body['ok'] === true &&
          mode === 'ok' &&
          choice.after !== undefined
        ) {
          if (!Object.hasOwn(fixture.status, choice.after))
            throw new Error(`No harvest sample ${choice.after}`);
          session.harvest = choice.after;
        }
        sendCase(res, answer);
      });
      req.on('error', () => {
        res.destroy();
      });
      return;
    }
    if (url.pathname.startsWith('/api/stream')) {
      session.preview ??= createPreviewFixtureState();
      if (servePreview(req, res, url, pages, scenario.preview ?? {}, session.preview)) return;
    }
    const scheduleReference =
      abilitiesRead(url, session.abilities) ??
      equipmentRead(url, session.equipment) ??
      scheduleRead(url, session.schedule);
    if (scheduleReference !== undefined) {
      sendCase(res, answerFor(scheduleReference));
      return;
    }
    if (
      session.history !== undefined &&
      (url.pathname === '/api/battles' ||
        url.pathname === '/api/actions/executions' ||
        /^\/api\/battles\/(?!active$)[^/]+$/.test(url.pathname))
    ) {
      sendCase(res, historyAnswer(url, session.history || undefined));
      return;
    }
    const id =
      GET_ROUTES[url.pathname] ??
      (url.pathname.startsWith('/api/template/') ? 'templateImage' : undefined) ??
      (url.pathname.startsWith('/api/artwork/') ? 'artwork' : undefined) ??
      (/^\/api\/battles\/[^/]+$/.test(url.pathname) ? 'battleDetail' : undefined) ??
      (/^\/api\/profile\/[^/]+\.yaml$/.test(url.pathname) ? 'profileRead' : undefined) ??
      (/^\/api\/profile-src\/[^/]+$/.test(url.pathname) ? 'profileSource' : undefined);
    if (id === undefined) {
      sendJson(res, 404, { ok: false, error: `${url.pathname} is not part of the fake backend` });
      return;
    }
    // Runtime event queries match the recorded request exactly (query ordering is immaterial).
    const canonicalQuery = (/** @type {URLSearchParams} */ query) =>
      JSON.stringify([...query].sort(([a], [b]) => a.localeCompare(b)));
    const diagnosticMatch =
      id === 'runtimeEvents' && url.searchParams.size > 0
        ? Object.entries(record(lookup(pages, 'runtime_diagnostics'))).find(([, value]) => {
            const request = record(record(value)['request']);
            const requestPath = request['path'];
            return (
              typeof requestPath === 'string' &&
              canonicalQuery(new URL(requestPath, 'http://fixture').searchParams) ===
                canonicalQuery(url.searchParams)
            );
          })?.[0]
        : undefined;
    const queryId =
      id === 'calibrate' && url.searchParams.has('operation_id')
        ? 'calibrateOperation'
        : id === 'scanPlan' && url.searchParams.has('preset')
          ? 'scanPlanPreset'
          : id;
    const reference =
      session.reads?.[url.pathname + url.search] ??
      session.reads?.[url.pathname] ??
      session.reads?.[queryId] ??
      session.reads?.[id] ??
      scenario.get?.[url.pathname + url.search] ??
      scenario.get?.[url.pathname] ??
      scenario.get?.[queryId] ??
      scenario.get?.[id] ??
      (diagnosticMatch === undefined ? undefined : `runtime_diagnostics.${diagnosticMatch}`) ??
      DEFAULT_GETS[queryId] ??
      DEFAULT_GETS[id];
    if (reference === '502') {
      sendFlaskError(res, 502);
      return;
    }
    if (
      reference?.startsWith('profile_target_collection_source:') ||
      reference?.startsWith('profile_target_collection_copy_source:')
    ) {
      const receipt = answerFor(`profile_target.${reference.split(':')[1]}`);
      const original = answerFor('profile_target.same_account_inactive_target_source');
      // Modified recorded GET: intermediate saves only record patch receipts. Keep the
      // source envelope and use exactly that receipt's persisted profile and revision.
      const body = {
        ...original.body,
        profile: structuredClone(receipt.body['profile']),
        revision: receipt.body['revision'],
      };
      if (reference.startsWith('profile_target_collection_copy_source:')) {
        // The recorded copy POST creates GATHER directly, with no source GET holding it.
        // Give all_on that recorded value to prove the browser copies the target source.
        const profile = record(body.profile);
        const policies = record(profile['policies']);
        record(policies['gather'])['all_on'] = record(
          lookup(pages, 'profile_target.inactive_gather_copy_saved.request.body'),
        )['value'];
      }
      sendJson(res, 200, body);
      return;
    }
    if (reference === 'profile_target_collection_settings') {
      const settings = answerFor('settings.populated_account_every_module');
      const runtime = record(
        lookup(pages, 'profile_target.inactive_gather_label_saved.request.body.expected_context'),
      );
      // Modified recorded settings: bind its existing active mine values to alpha,
      // the profile_target world's account. No field value or module is changed.
      sendJson(res, settings.status, {
        ...settings.body,
        context: { ...record(settings.body['context']), account: runtime['account'] },
      });
      return;
    }
    const fencedSource = profilePatchFencedRead(reference);
    if (fencedSource !== undefined) {
      sendCase(res, fencedSource);
      return;
    }
    const librarySource = shoppingLibrarySource(reference);
    if (librarySource !== undefined) {
      sendCase(res, librarySource);
      return;
    }
    const harvest = structuredClone(record(fixture.status[session.harvest ?? scenario.harvest]));
    if (scenario.operation !== undefined) {
      // Fields of the harvest operation no fixture sample holds (a missing revision).
      harvest['operation'] = { ...record(harvest['operation']), ...scenario.operation };
    }
    if (id === 'harvest') {
      if (reference === undefined) sendJson(res, 200, { ok: true, harvest });
      else sendCase(res, answerFor(reference));
      return;
    }
    if (id === 'battleDetail' && reference === undefined) {
      const battleId = decodeURIComponent(url.pathname.slice('/api/battles/'.length));
      const cases = record(record(dashboard)['battle_detail']);
      const matched = Object.keys(cases).find(
        (key) => lookup(cases, `${key}.body.battle.id`) === battleId,
      );
      sendCase(res, fixtureCase(cases, matched ?? 'unknown_battle'));
      return;
    }
    if (reference === undefined) throw new Error(`No default GET case for ${id}`);
    const answer = answerFor(reference);
    if (
      name.startsWith('profile_target_abilities_') &&
      id === 'profileSource' &&
      answer.body['name'] === undefined
    ) {
      // Modified recorded case: expose a preceding patch's profile/revision as
      // a later source read. Reuse the recorded source envelope and exact saved data.
      answer.body = {
        ...answerFor('profile_target.same_account_inactive_target_source').body,
        ...answer.body,
        name: decodeURIComponent(url.pathname.split('/').at(-1) ?? ''),
      };
    }
    if (
      name === 'shopping_saved_create' &&
      id === 'runControls' &&
      reference === 'run_controls_save.read_before_the_plan_save'
    ) {
      // SQ1: the no-plan recording predates the queue-save recording's revision/interval.
      // Keep its null plan; align only revision and routine interval to the recorded empty save
      // so the journey can assert that exact request and receipt without inventing an answer.
      const before = answerFor('shopping_queue.shopping_read_before_any_save');
      answer.body['profile_revision'] = before.body['profile_revision'];
      record(record(record(answer.body['settings'])['routines'])['shopping'])['interval_sec'] =
        record(record(record(before.body['settings'])['routines'])['shopping'])['interval_sec'];
    }
    if (id === 'status' && answer.status === 200) {
      answer.body['harvest'] = harvest;
      if (scenario.scan !== 'fixture') {
        const serverTime = harvest['server_time'];
        if (typeof serverTime !== 'number') throw new Error('Harvest fixture has no server_time');
        answer.body['process_refresh'] = {
          ...record(answer.body['process_refresh']),
          state: 'fresh',
          error: null,
          age_sec: 0,
          last_success_at: serverTime,
          last_attempt_at: serverTime,
        };
      }
      if (scenario.processes !== undefined) {
        // A process list no fixture case holds (spec 9.1 X9's malformed linked row).
        answer.body['processes'] = structuredClone(scenario.processes);
      }
      if (scenario.linkedCollector === true) {
        const linked = answerFor('status.processes_linked_collector').body['processes'];
        const row = record(Array.isArray(linked) ? linked[0] : undefined);
        const operation = record(harvest['operation']);
        const fullOperation = {
          ...record(row['operation']),
          id: operation['id'],
          revision: operation['revision'],
          status: operation['status'],
        };
        answer.body['processes'] = [
          {
            ...row,
            operation: fullOperation,
            operation_id: operation['id'],
            operation_revision: operation['revision'],
          },
        ];
      }
    }
    sendCase(res, answer);
  };
}

/**
 * @param {Response} res
 * @param {string} buildDir
 * @param {string} pathname
 */
function serveBuild(res, buildDir, pathname) {
  const relative = decodeURIComponent(pathname.slice('/ui/app/'.length)) || 'index.html';
  const file = path.resolve(buildDir, relative);
  if (!file.startsWith(buildDir + path.sep) || !existsSync(file) || !statSync(file).isFile()) {
    res.statusCode = 404;
    res.end('Not found');
    return;
  }
  res.statusCode = 200;
  res.setHeader('Content-Type', CONTENT_TYPES[path.extname(file)] ?? 'application/octet-stream');
  createReadStream(file).pipe(res);
}

/**
 * The standalone server used by Playwright: built app plus fixture API.
 * @param {HandlerOptions & { port: number, buildDir?: string, fixturePath?: string }} options
 */
export function startServer(options) {
  refuseRealTarget(options);
  const buildDir = options.buildDir ?? BUILD_DIR;
  if (!existsSync(path.join(buildDir, 'index.html'))) {
    throw new Error(
      `The app is not built (${buildDir}). Run: cd frontend/app; npm ci; npm run build`,
    );
  }
  const api = createApiHandler(loadFixture(options.fixturePath), options);
  const server = createServer((req, res) => {
    const pathname = new URL(req.url ?? '/', 'http://fake-backend').pathname;
    if (pathname.startsWith('/api/') || pathname.startsWith('/__fake/')) {
      api(req, res);
    } else if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      res.end();
    } else if (pathname === '/' || pathname === '/ui/app') {
      res.statusCode = 302;
      res.setHeader('Location', '/ui/app/');
      res.end();
    } else if (pathname.startsWith('/ui/app/')) {
      serveBuild(res, buildDir, pathname);
    } else {
      res.statusCode = 404;
      res.end('Not found');
    }
  });
  server.on('connection', resetAfterClientClose);
  server.listen(options.port, '127.0.0.1');
  return server;
}

/**
 * Close a connection the client has finished with by reset, not by FIN.
 *
 * Every Playwright test opens a fresh browser context with up to six keep-alive connections
 * and closes them when the test ends. The side that closes first keeps its socket in TIME_WAIT
 * (two minutes on Windows): a full eight-worker run held about 4,700 of them on the client
 * side of this port, and one run lost the app stylesheet to net::ERR_NO_BUFFER_SPACE. Answering
 * the client's FIN with a reset ends both sides without TIME_WAIT. It runs only after the client
 * has closed its side, so no answer it could still read is cut, and the deliberate drops
 * (`req.socket.destroy()` before the client closes) are unchanged.
 * @param {import('node:net').Socket} socket
 */
export function resetAfterClientClose(socket) {
  // Before http's own 'end' listener, which would otherwise send the FIN that leaves TIME_WAIT.
  socket.prependListener('end', () => {
    socket.resetAndDestroy();
  });
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const args = process.argv.slice(2);
  /** @param {string} name */
  const option = (name) => {
    const at = args.indexOf(name);
    return at >= 0 ? args[at + 1] : undefined;
  };
  const port = Number(option('--port') ?? '4174');
  const scenario = option('--scenario');
  const target = option('--target') ?? option('--real-backend');
  const buildDir = option('--build-dir');
  const server = startServer({
    port,
    ...(scenario === undefined ? {} : { scenario }),
    ...(target === undefined ? {} : { target }),
    ...(buildDir === undefined ? {} : { buildDir: path.resolve(buildDir) }),
  });
  server.on('listening', () => {
    process.stdout.write(`fake backend on http://127.0.0.1:${port}/ui/app/\n`);
  });
}
