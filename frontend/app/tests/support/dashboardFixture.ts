import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';

/**
 * The Dashboard contract fixture (backend/tests/fixtures/dashboard_contract_v1.json,
 * spec 10.3) as the core tests read it. Every case is
 * `{request: {method, path, body?}, status, body}`; cases are addressed as
 * `<family>.<case>`, e.g. `status.processes_untracked_created_null` or
 * `battles_active.commands_controls_pending_some_paths_applied_action_completed`.
 * Returned values are fresh deep copies. TP_CONTRACT_FIXTURE switches only the
 * harvest fixture (spec 10.4), never this file.
 */
const LOADED = loadJsonWithIdentity(DASHBOARD_FIXTURE_PATH);
const CASES: unknown = (LOADED.fixture as { readonly cases?: unknown }).cases;

export const DASHBOARD_FIXTURE_FILE: string = DASHBOARD_FIXTURE_PATH;
export const DASHBOARD_FIXTURE_SHA256: string = LOADED.identity.sha256;

function lookup(path: string): unknown {
  let current: unknown = CASES;
  for (const key of path.split('.')) {
    if (typeof current !== 'object' || current === null || !Object.hasOwn(current, key)) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

export interface DashboardCase {
  readonly request: { readonly method: string; readonly path: string; readonly body?: unknown };
  readonly status: number;
  readonly body: unknown;
}

/** A deep copy of the case `<family>.<case>`; throws naming it when the fixture lacks it. */
export function dashboardCase(path: string): DashboardCase {
  const found = lookup(path);
  if (typeof found !== 'object' || found === null || Array.isArray(found)) {
    throw new Error(`The Dashboard contract fixture ${DASHBOARD_FIXTURE_PATH} has no case ${path}`);
  }
  return JSON.parse(JSON.stringify(found)) as DashboardCase;
}

/** A deep copy of the body of the case `<family>.<case>`. */
export function dashboardBody(path: string): unknown {
  return dashboardCase(path).body;
}

/** Every case family of the fixture, sorted. */
export function dashboardFamilies(): readonly string[] {
  return typeof CASES === 'object' && CASES !== null ? Object.keys(CASES).sort() : [];
}

/** The case names of a family, sorted. */
export function dashboardCaseNames(family: string): readonly string[] {
  const found = lookup(family);
  if (typeof found !== 'object' || found === null) {
    throw new Error(
      `The Dashboard contract fixture ${DASHBOARD_FIXTURE_PATH} has no family ${family}`,
    );
  }
  return Object.keys(found).sort();
}

/**
 * The pages contract fixture (backend/tests/fixtures/dashboard_contract_pages_v1.json):
 * the same case shape, addressed `<family>.<case>` (e.g.
 * `run_controls_save.saved_switch_off`). Returned values are fresh deep copies.
 */
const PAGES_CASES: unknown = (
  loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture as { readonly cases?: unknown }
).cases;

/** A deep copy of the pages case `<family>.<case>`; throws naming it when the fixture lacks it. */
export function dashboardPagesCase(path: string): DashboardCase {
  let current: unknown = PAGES_CASES;
  for (const key of path.split('.')) {
    if (typeof current !== 'object' || current === null || !Object.hasOwn(current, key)) {
      throw new Error(
        `The pages contract fixture ${DASHBOARD_PAGES_FIXTURE_PATH} has no case ${path}`,
      );
    }
    current = (current as Record<string, unknown>)[key];
  }
  return JSON.parse(JSON.stringify(current)) as DashboardCase;
}

/** The case names of a pages family, sorted. */
export function dashboardPagesCaseNames(family: string): readonly string[] {
  const found =
    typeof PAGES_CASES === 'object' && PAGES_CASES !== null
      ? (PAGES_CASES as Record<string, unknown>)[family]
      : undefined;
  if (typeof found !== 'object' || found === null)
    throw new Error(
      `The pages contract fixture ${DASHBOARD_PAGES_FIXTURE_PATH} has no family ${family}`,
    );
  return Object.keys(found).sort();
}
