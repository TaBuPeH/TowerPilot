import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import { copy } from '../../../src/copy/catalogue.ts';
import { ThisBattle } from '../../../src/features/dashboard/thisBattle/ThisBattle.tsx';
import { BattleDetails } from '../../../src/features/runs/BattleDetails.tsx';
import { RunEditor } from '../../../src/features/runs/RunEditor.tsx';
import { runControlsPath } from '../../../src/store/selectors/gemSlot.ts';
import { selectRunsPage } from '../../../src/store/selectors/runs.ts';
import { selectThisBattle } from '../../../src/store/selectors/thisBattle.ts';
import type { DashboardState } from '../../../src/store/types.ts';
import { renderRegion } from '../features/dashboard/harness.tsx';
import type { DashboardCase } from '../../support/dashboardFixture.ts';
import { addBody, addCase, regionState } from './regionHelpers.ts';

/**
 * A2-7 output dump (development rules: refactor, then change). Every recorded
 * GET /api/battles/active case of the checkout fixtures (dashboard_contract_v1.json and
 * dashboard_contract_pages_v1.json), with the battle run's saved bounds read as the Runs page
 * reads them, is serialized as the This battle and Runs live view models and the rendered This
 * battle, live editor and battle details; every recorded GET /api/run-controls read is
 * serialized as the saved editor. The original snapshot stayed byte-identical through the
 * behaviour-preserving R1 and R2 steps. The AC1/AC2/AC3/AC5 behavior phase deliberately
 * replaces that baseline: descriptor rows/fields, result/effect output and shared reading gates.
 * The reviewed per-case diff is recorded in frontend-a2-7.json ac1_ac2_finalization. The landed r17b's
 * projection cases are the P:battle_actions_projection rows. The dump is a refactor gate over the
 * recordings as they are: when the fixtures change or a C step changes behaviour by
 * design, it is regenerated deliberately (vitest -u) and the diff reviewed with that change.
 */
type Fixture = Readonly<Record<string, Readonly<Record<string, DashboardCase>>>>;

function casesOf(file: string): Fixture {
  return Reflect.get(Object(loadJsonWithIdentity(file).fixture), 'cases') as Fixture;
}

const CHECKOUT: readonly (readonly [string, Fixture])[] = [
  ['D', casesOf(DASHBOARD_FIXTURE_PATH)],
  ['P', casesOf(DASHBOARD_PAGES_FIXTURE_PATH)],
];
/** The one recorded saved read whose constraints every recorded battle's run shares. */
const BOUNDS = casesOf(DASHBOARD_PAGES_FIXTURE_PATH)['run_controls_save']?.[
  'read_after_a_lost_answer_a_new_revision_without_the_sent_values'
];
const CATALOGUE = casesOf(DASHBOARD_FIXTURE_PATH)['runs']?.['defined_and_undefined_with_labels'];

function reads(
  fixtures: readonly (readonly [string, Fixture])[],
  match: (found: DashboardCase) => boolean,
): readonly (readonly [string, DashboardCase])[] {
  return fixtures.flatMap(([tag, cases]) =>
    Object.entries(cases)
      .sort(([a], [b]) => a.localeCompare(b))
      .flatMap(([family, rows]) =>
        Object.entries(rows)
          .sort(([a], [b]) => a.localeCompare(b))
          .filter(([, found]) => typeof found.request === 'object' && match(found))
          .map(([name, found]) => [`${tag}:${family}.${name}`, found] as const),
      ),
  );
}

const battleRead = (found: DashboardCase) =>
  found.request.method === 'GET' && found.request.path.startsWith('/api/battles/active');

/** React's generated ids, renumbered in order of appearance (stable whatever rendered before). */
function html(container: HTMLElement): string {
  const ids = new Map<string, string>();
  return container.innerHTML.replace(/«[^»]*»|:r[0-9a-z]+:|_r_[0-9a-z]+_/g, (found) => {
    const next = ids.get(found) ?? `«id${String(ids.size)}»`;
    ids.set(found, next);
    return next;
  });
}

function rendered(state: DashboardState, element: Parameters<typeof renderRegion>[1]): string {
  const h = renderRegion(state, element);
  const out = html(h.view.container);
  h.view.unmount();
  return out;
}

function battleState(found: DashboardCase): DashboardState {
  let state: DashboardState = { ...regionState(), contextConfirmed: true };
  const blueprint: unknown = Reflect.get(
    Object(Reflect.get(Object(found.body), 'battle')),
    'blueprint',
  );
  if (typeof blueprint === 'string' && BOUNDS !== undefined)
    state = addBody(state, 'runControls', BOUNDS.body, { path: runControlsPath(blueprint) });
  const path = found.request.path;
  return found.status >= 500
    ? addCase(state, 'battlesActive', 'battles_active.stale_report') // never reached: none recorded
    : addBody(state, 'battlesActive', found.body, { path, status: found.status });
}

function battleDump(name: string, found: DashboardCase) {
  if (found.status >= 500) throw new Error(`${name}: a 5xx battle read is not dumped`);
  const state = battleState(found);
  const thisBattle = selectThisBattle(state);
  const live = selectRunsPage(state).live;
  return {
    case: name,
    thisBattle,
    live,
    thisBattleHtml: rendered(state, <ThisBattle />),
    liveEditorHtml:
      live.editor === null
        ? null
        : rendered(
            state,
            <RunEditor
              editor={live.editor}
              live
              saveLabel={copy.runs.live.save}
              onSave={() => undefined}
            />,
          ),
    detailsHtml: rendered(
      state,
      <BattleDetails details={live.details} lastKnown={live.detailsLastKnown} />,
    ),
  };
}

function savedDump(name: string, found: DashboardCase) {
  const blueprint = new URL(found.request.path, 'http://fixture').searchParams.get('blueprint');
  if (blueprint === null || CATALOGUE === undefined) throw new Error(`${name}: no blueprint`);
  let state: DashboardState = {
    ...regionState(),
    contextConfirmed: true,
    runsRun: blueprint,
    runsOpen: true,
  };
  state = addBody(state, 'runs', CATALOGUE.body, { path: '/api/runs' });
  state = addBody(state, 'runControls', found.body, { path: runControlsPath(blueprint) });
  const saved = selectRunsPage(state).saved;
  return {
    case: name,
    saved,
    savedEditorHtml:
      saved.editor === null
        ? null
        : rendered(
            state,
            <RunEditor
              editor={saved.editor}
              live={false}
              saveLabel={copy.runs.saved.save}
              onSave={() => undefined}
            />,
          ),
  };
}

function dumpText(value: unknown): string {
  return `${JSON.stringify(value, null, 1)}\n`;
}

describe('A2-7 reviewed behavior output dump', () => {
  it('dumps every recorded checkout battle read and saved read against the reviewed baseline', async () => {
    const battles = reads(CHECKOUT, battleRead);
    const saved = reads(
      CHECKOUT,
      (found) =>
        found.request.method === 'GET' &&
        found.request.path.startsWith('/api/run-controls') &&
        found.status === 200,
    );
    expect(battles.length).toBe(65);
    expect(saved.length).toBe(46);
    const text = dumpText({
      battles: battles.map(([name, found]) => battleDump(name, found)),
      saved: saved.map(([name, found]) => savedDump(name, found)),
    });
    await expect(text).toMatchFileSnapshot('./__dumps__/actions-dump.json');
  }, 120_000);

  it('R2 a projection gives the live editor its bounds before the saved run is read', () => {
    const live = (found: DashboardCase) => {
      const state = addBody(
        { ...regionState(), contextConfirmed: true },
        'battlesActive',
        found.body,
        {
          path: found.request.path,
        },
      );
      const page = selectRunsPage(state).live;
      const tap = page.editor?.groups
        .flatMap((group) => group.fields)
        .find((field) => field.path === 'routines.floating_gems.tap_interval_sec');
      return { boundsKnown: page.boundsKnown, min: tap?.min, max: tap?.max };
    };
    const pages = casesOf(DASHBOARD_PAGES_FIXTURE_PATH);
    const projected =
      pages['battle_actions_projection']?.['idle_ad_and_floating_gems_due_shopping_waiting'];
    const compat =
      pages['battle_actions_projection']?.['projection_never_built_the_actions_key_is_absent'];
    if (projected === undefined || compat === undefined) throw new Error('missing recorded case');
    expect(live(projected)).toEqual({ boundsKnown: true, min: 0.5, max: 30 });
    expect(live(compat)).toEqual({ boundsKnown: false, min: null, max: null });
  });
});
