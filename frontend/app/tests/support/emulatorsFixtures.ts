import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from './dashboardFixture.ts';

/** The members these tests read of the pages fixture's emulator_bluestacks_inventory cases. */
interface BlueStacksCase extends DashboardCase {
  readonly request: { readonly method: string; readonly path: string; readonly body?: unknown };
  readonly body: {
    readonly message: string;
    readonly backup: string;
    readonly instances: readonly Record<string, unknown>[];
    readonly [member: string]: unknown;
  };
}

/** The pages fixture's emulator_bluestacks_inventory family (recorded in r12), by case name. */
const blueStacksCases: Readonly<Record<string, BlueStacksCase>> = Object.fromEntries(
  dashboardPagesCaseNames('emulator_bluestacks_inventory').map((name) => [
    name,
    dashboardPagesCase(`emulator_bluestacks_inventory.${name}`) as BlueStacksCase,
  ]),
);

export function blueStacksCase(name: string): BlueStacksCase {
  const found = blueStacksCases[name];
  if (found === undefined) throw new Error(`No recorded emulator_bluestacks_inventory.${name}`);
  return found;
}

export const blueStacksMixed = blueStacksCase(
  'one_stopped_one_running_instance_adb_off_and_a_display_that_is_not_ok',
);

export function stoppedBlueStacks(): DashboardCase {
  // modified: no recorded case has every instance stopped (before Prepare is needed).
  // Explicitly authorized for unit and browser consent checks; all POST answers stay recorded.
  return {
    ...blueStacksMixed,
    body: {
      ...blueStacksMixed.body,
      running: [],
      suggestion: null,
      instances: blueStacksMixed.body.instances.map((instance) => ({
        ...instance,
        running: false,
      })),
    },
  };
}

export function firstInstallConfig(boundAccount: boolean): DashboardCase {
  const found = dashboardPagesCase(
    boundAccount
      ? 'config_state.populated_accounts_and_loadouts'
      : 'config_state.read_before_the_save',
  );
  const body = found.body as { readonly instances: Readonly<Record<string, object>> };
  // modified: no recorded config case has an empty serial. Same modification as the parent
  // F6M-C-01 unit test, explicitly authorized for browser checks, including a bound account.
  return {
    ...found,
    body: {
      ...body,
      instances: { ...body.instances, main: { ...body.instances['main'], serial: '' } },
    },
  };
}
