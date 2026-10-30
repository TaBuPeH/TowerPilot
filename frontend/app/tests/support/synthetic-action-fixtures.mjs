// @ts-check
import {
  SYNTHETIC_ACTION_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import { record } from '../../scripts/generate-contract.mjs';

const loaded = loadJsonWithIdentity(SYNTHETIC_ACTION_FIXTURE_PATH);
/** @typedef {{request: {method: string, path: string, body?: unknown}, status: number, body: Record<string, unknown>}} SyntheticCase */
const cases = /** @type {{cases: {synthetic_action: Record<string, SyntheticCase>}}} */ (
  loaded.fixture
).cases.synthetic_action;

/** A fresh B1 recording, using the shared checkout loader. @param {string} name */
export function syntheticCase(name) {
  const found = cases[name];
  if (found === undefined) throw new Error(`Missing B1 recording ${name}`);
  return structuredClone(found);
}

/**
 * The GET shape before a recorded write. B1 records the post-write read/receipt;
 * remove only that write's command to replay the moment before it was sent.
 * No domain values or request fences are invented.
 * @param {'run' | 'unknown'} kind
 */
export function syntheticBefore(kind) {
  const name = kind === 'run' ? 'synthetic_run_now_accepted' : 'synthetic_run_now_reply_unknown';
  const write = syntheticCase(name);
  const read = syntheticCase(
    kind === 'run' ? 'synthetic_run_now_read_started' : 'synthetic_run_now_reply_unknown_read',
  );
  const body = read.body;
  const battle = record(kind === 'run' ? write.body['battle'] : body['battle']);
  const key = record(write.request.body)['idempotency_key'];
  battle['commands'] = /** @type {Record<string, unknown>[]} */ (battle['commands']).filter(
    (command) => !(/** @type {unknown[]} */ (command['request_keys'] ?? []).includes(key)),
  );
  body['battle'] = battle;
  return read;
}

/** Test-local stale projection context; B1 has a stale POST, but no stale-context GET. */
export function syntheticStaleContext() {
  const read = syntheticCase('synthetic_active_two_instances');
  const battle = record(read.body['battle']);
  const runtime = record(battle['runtime']);
  const actions = record(runtime['actions']);
  actions['context'] = { ...record(actions['context']), profile: 'previous-profile' };
  runtime['actions'] = actions;
  battle['runtime'] = runtime;
  read.body['battle'] = battle;
  return read;
}

/** Fake-backend reference resolution, kept in the test fixture layer.
 * @param {string} reference */
export function syntheticAnswer(reference) {
  const name = reference.slice('b1:'.length);
  if (name === 'before_run') return syntheticBefore('run');
  if (name === 'before_unknown') return syntheticBefore('unknown');
  if (name === 'stale_context') return syntheticStaleContext();
  return syntheticCase(name);
}
