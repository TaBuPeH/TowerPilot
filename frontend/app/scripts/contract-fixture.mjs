// @ts-check
/**
 * The backend's harvest status contract fixture (ADR-001 rule 8), read in one
 * place by the contract tests, the fake backend and the dev server.
 *
 * The fixture is produced by the backend (backend/tests/unit/test_harvest_contract.py)
 * and must be committed together with frontend/app: without it the contract
 * tests, `npm run dev` and the Playwright suite cannot run. A missing file
 * fails loudly with this name and that instruction, never with a bare ENOENT.
 */
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Repository-relative path of the fixture. */
export const FIXTURE_NAME = 'backend/tests/fixtures/harvest_status_v1.json';
export const DEFAULT_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
  FIXTURE_NAME,
);

/**
 * Pure selection/validation: environment and filesystem observations are inputs.
 * A drive prefix is a path, never a URL scheme. Empty, set values also fail.
 * @param {string | undefined} value
 * @param {(file: string) => boolean} isRegularFile
 * @returns {string}
 */
export function resolveFixturePath(value, isRegularFile) {
  if (value === undefined) return DEFAULT_FIXTURE_PATH;
  const drive = /^[a-z]:[\\/]/i.test(value);
  if ((!drive && /^[a-z][a-z\d+.-]*:/i.test(value)) || !isRegularFile(value)) {
    throw new Error(`TP_CONTRACT_FIXTURE must name an existing regular file, got ${value}`);
  }
  return value;
}

export const FIXTURE_PATH = resolveFixturePath(
  process.env['TP_CONTRACT_FIXTURE'],
  (file) => existsSync(file) && statSync(file).isFile(),
);

/** The Dashboard contract fixture (spec 10.3, OD-2); not switched by TP_CONTRACT_FIXTURE. */
export const DASHBOARD_FIXTURE_NAME = 'backend/tests/fixtures/dashboard_contract_v1.json';
export const DASHBOARD_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
  DASHBOARD_FIXTURE_NAME,
);

/**
 * The pages contract fixture (Runs and the pages after it). The backend appends
 * families to it; a family keeps its content (and its hash) when another is
 * appended, so its consumers are bound to family hashes, never the file hash.
 */
export const DASHBOARD_PAGES_FIXTURE_NAME =
  'backend/tests/fixtures/dashboard_contract_pages_v1.json';
export const DASHBOARD_PAGES_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
  DASHBOARD_PAGES_FIXTURE_NAME,
);

/** Test-only route recordings; never added to the app's generated contracts (AC7 is separate). */
export const SYNTHETIC_ACTION_FIXTURE_NAME =
  'backend/tests/fixtures/synthetic_action_contract_v1.json';
export const SYNTHETIC_ACTION_FIXTURE_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
  SYNTHETIC_ACTION_FIXTURE_NAME,
);

/** A number as the fixture writes it (Python's own text: 60.0 stays 60.0). */
class RawNumber {
  /** @param {string} text */
  constructor(text) {
    this.text = text;
  }

  /** The number exactly as written. */
  toString() {
    return this.text;
  }
}

/**
 * Python's `json.dumps(value, sort_keys=True, ensure_ascii=False)` of a value
 * parsed with its numbers kept as written.
 * @param {unknown} value
 * @returns {string}
 */
function pythonDumps(value) {
  if (value === null) return 'null';
  if (value === true) return 'true';
  if (value === false) return 'false';
  if (typeof value === 'string') return JSON.stringify(value);
  if (value instanceof RawNumber) return value.toString();
  if (Array.isArray(value)) return `[${value.map(pythonDumps).join(', ')}]`;
  if (typeof value === 'object') {
    const entries = Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, member]) => `${JSON.stringify(key)}: ${pythonDumps(member)}`).join(', ')}}`;
  }
  throw new Error(`Not a JSON value: ${typeof value}`);
}

/**
 * A JSON.parse reviver that keeps each number's source text (JSON.parse source text access).
 * @param {string} _key @param {unknown} value @param {{ source?: string }} [context]
 * @returns {unknown}
 */
function keepNumberText(_key, value, context) {
  if (typeof value !== 'number') return value;
  if (context?.source === undefined) throw new Error('JSON.parse gives no number source text');
  return new RawNumber(context.source);
}

/**
 * Each family's hash as the backend computes it (test_dashboard_contract_pages.py
 * `family_hashes`): SHA-256 of `json.dumps(cases, sort_keys=True,
 * ensure_ascii=False)`, upper-case hex.
 * @param {string} file
 * @returns {Record<string, string>}
 */
export function familyHashes(file) {
  if (!existsSync(file)) throw new MissingContractFixture(file);
  /** @type {unknown} */
  const parsed = JSON.parse(readFileSync(file, 'utf8'), keepNumberText);
  /** @type {unknown} */
  const cases = typeof parsed === 'object' && parsed !== null ? Reflect.get(parsed, 'cases') : null;
  if (typeof cases !== 'object' || cases === null) throw new Error(`No cases in ${file}`);
  return Object.fromEntries(
    Object.entries(cases).map(([family, value]) => [
      family,
      createHash('sha256').update(pythonDumps(value), 'utf8').digest('hex').toUpperCase(),
    ]),
  );
}

/**
 * Each fixture's repository name and the backend test that generates it, by file name.
 * @type {ReadonlyMap<string, readonly [string, string]>}
 */
const FIXTURE_SOURCES = new Map([
  ['harvest_status_v1.json', [FIXTURE_NAME, 'backend/tests/unit/test_harvest_contract.py']],
  [
    'dashboard_contract_v1.json',
    [DASHBOARD_FIXTURE_NAME, 'backend/tests/unit/test_dashboard_contract.py'],
  ],
  [
    'dashboard_contract_pages_v1.json',
    [DASHBOARD_PAGES_FIXTURE_NAME, 'backend/tests/unit/test_dashboard_contract_pages.py'],
  ],
  [
    'synthetic_action_contract_v1.json',
    [SYNTHETIC_ACTION_FIXTURE_NAME, 'backend/tests/unit/test_synthetic_action_contract.py'],
  ],
]);

/** @typedef {{ version: number, status: Record<string, unknown> }} HarvestFixture */

export class MissingContractFixture extends Error {
  /** @param {string} file */
  constructor(file) {
    const [name, generator] = FIXTURE_SOURCES.get(path.basename(file)) ?? [
      FIXTURE_NAME,
      'backend/tests/unit/test_harvest_contract.py',
    ];
    super(
      `Contract fixture missing: ${name} (looked for ${file}). ` +
        'The dashboard app contract tests, fake backend and dev server read it (ADR-001 rule 8). ' +
        'It must be committed together with frontend/app; check `git ls-files ' +
        name +
        `\` and restore it from the backend change that generates it (${generator}).`,
    );
    this.name = 'MissingContractFixture';
  }
}

/**
 * @param {string} [file]
 * @returns {HarvestFixture}
 */
export function loadFixture(file = FIXTURE_PATH) {
  return loadFixtureWithIdentity(file).fixture;
}

/**
 * Parse and identify the same byte buffer (no reserialization or second read).
 * @param {string} [file]
 */
export function loadFixtureWithIdentity(file = FIXTURE_PATH) {
  const loaded = loadJsonWithIdentity(file);
  if (!isFixture(loaded.fixture)) {
    throw new Error(
      `Unexpected contract fixture shape in ${file}: expected {version: 1, status: {...}}`,
    );
  }
  return { fixture: loaded.fixture, identity: loaded.identity };
}

/** A second fixture can share byte identity without pretending to be HarvestFixture.
 * @param {string} file
 */
export function loadJsonWithIdentity(file) {
  if (!existsSync(file)) {
    throw new MissingContractFixture(file);
  }
  const bytes = readFileSync(file);
  /** @type {unknown} */
  const parsed = JSON.parse(bytes.toString('utf8'));
  return {
    fixture: parsed,
    identity: { path: file, sha256: createHash('sha256').update(bytes).digest('hex') },
  };
}

/** @param {string} [file] */
export function fixtureIdentity(file = FIXTURE_PATH) {
  return loadFixtureWithIdentity(file).identity;
}

/**
 * @param {unknown} value
 * @returns {value is HarvestFixture}
 */
function isFixture(value) {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  /** @type {unknown} */
  const version = Reflect.get(value, 'version');
  /** @type {unknown} */
  const status = Reflect.get(value, 'status');
  return version === 1 && typeof status === 'object' && status !== null;
}
