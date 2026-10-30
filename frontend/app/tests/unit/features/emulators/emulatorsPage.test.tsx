import { screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { postBodies } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';
import {
  blueStacksCase,
  blueStacksMixed,
  stoppedBlueStacks,
} from '../../../support/emulatorsFixtures.ts';

/**
 * The Emulators page (backend/logs/reviews/ui-pages/emulators-brief.md, M1 to M8) on the
 * whole wired app with a fake transport. Every answer is a recorded case: the pages
 * contract (emulator_actions, emulator_inventory, emulator_tools, settings, config_state)
 * or the Dashboard contract (wizard_emulators, wizard_tools, wizard_state, reconnect).
 */
const pages = (path: string) => dashboardPagesCase(path);
const v1 = (path: string) => dashboardCase(path);

function answerOf(found: DashboardCase): { readonly status: number; readonly body: unknown } {
  const body = found.body as Record<string, unknown>;
  return {
    status: found.status,
    body: Object.keys(body).length === 1 && typeof body['text'] === 'string' ? body['text'] : body,
  };
}

type Reads = Partial<
  Record<'config' | 'scan' | 'tools' | 'state' | 'resolution', () => DashboardCase | 'hold'>
>;

async function openEmulators(reads: Reads = {}, options: PageOptions = {}) {
  window.location.hash = '#emulators';
  const read = (name: keyof Reads, fallback: () => DashboardCase) => () => {
    const found = (reads[name] ?? fallback)();
    return found === 'hold' ? 'hold' : answerOf(found);
  };
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    ...options,
    before: (fake) => {
      fake.route(
        'GET',
        '/api/config',
        read('config', () => pages('config_state.read_before_the_save')),
      );
      fake.route(
        'GET',
        '/api/wizard/emulators',
        read('scan', () => v1('wizard_emulators.instances_with_names_and_ports')),
      );
      fake.route(
        'GET',
        '/api/wizard/tools',
        read('tools', () => pages('emulator_tools.tools_idle_before_any_install')),
      );
      fake.route(
        'GET',
        '/api/wizard/state',
        read('state', () => v1('wizard_state.empty')),
      );
      fake.route(
        'GET',
        /^\/api\/wizard\/resolution/u,
        read('resolution', () => pages('emulator_inventory.resolution_portrait_reference')),
      );
      fake.route('GET', '/api/settings', () =>
        answerOf(pages('settings.populated_account_every_module')),
      );
      options.before?.(fake);
    },
  });
  await page.advance(1_000);
  return page;
}

type Page = Awaited<ReturnType<typeof openEmulators>>;

function region(
  name: 'emulatorsTarget' | 'emulatorsDetected' | 'emulatorsTools' | 'emulatorsPreview',
) {
  const found = document.querySelector<HTMLElement>(`[data-region="${name}"]`);
  if (found === null) throw new Error(`${name} missing`);
  return found;
}

function row(key: string): HTMLElement {
  const found = document.querySelector<HTMLElement>(`[data-row="${key}"]`);
  if (found === null) throw new Error(`row ${key} missing`);
  return found;
}

const posts = (page: Page, path: string) => postBodies(page.fake, path);
const requests = (page: Page, prefix: string) =>
  page.fake.requests.filter((request) => request.path.startsWith(prefix));

function dialog(): HTMLElement {
  const found = document.querySelector<HTMLElement>('dialog[open]');
  if (found === null) throw new Error('no open dialog');
  return found;
}

/** Open one help icon's tip as a mouse does, read it, and close it again with Escape. */
async function tipOf(page: Page, inside: HTMLElement, subject: string): Promise<string> {
  await page.user.hover(within(inside).getByRole('button', { name: `About ${subject}` }));
  const text = screen.getByRole('tooltip').textContent;
  await page.user.keyboard('{Escape}');
  return text;
}

// A whole app per test.
vi.setConfig({ testTimeout: 20_000 });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('M1 discovery: what the last scan saw, kept apart from configuration and connection', () => {
  it('shows a matching suggestion once on its detected row, with a single Use action', async () => {
    const page = await openEmulators();
    const suggestion = region('emulatorsDetected').querySelector('[data-suggestion]');
    expect(suggestion).toBe(row('MuMu:0'));
    expect(within(row('MuMu:0')).getAllByRole('button', { name: 'Use this one' })).toHaveLength(1);
    expect(within(row('MuMu:0')).getByText('Suggested', { exact: true })).toBeVisible();
    expect(page.fake.writes()).toEqual([]);
  });

  it.each(['unmatched', 'in use'] as const)(
    'keeps an %s suggestion and its own action gating',
    async (state) => {
      const found = v1('wizard_emulators.instances_with_names_and_ports');
      const body = found.body as {
        suggestion: { serial: string };
        configured: readonly { serial: string }[];
      };
      // Modified recording: no recorded scan covers a suggestion outside the detected rows
      // or a suggestion already configured by another instance. Preserve the other scan facts.
      const page = await openEmulators({
        scan: () => ({
          ...found,
          body:
            state === 'unmatched'
              ? { ...body, suggestion: { ...body.suggestion, serial: 'unlisted:5555' } }
              : {
                  ...body,
                  configured: [
                    ...body.configured,
                    { instance: 'another', serial: body.suggestion.serial, port_open: true },
                  ],
                },
        }),
      });
      const suggestion =
        region('emulatorsDetected').querySelector<HTMLElement>('[data-suggestion]');
      if (suggestion === null) throw new Error('Suggestion missing');
      const use = within(suggestion).getByRole('button', { name: 'Use this one' });
      if (state === 'unmatched') {
        expect(suggestion).not.toHaveAttribute('data-row');
        expect(suggestion).toHaveTextContent('unlisted:5555');
        expect(use).not.toHaveAttribute('aria-disabled');
      } else {
        expect(suggestion).toBe(row('MuMu:0'));
        expect(use).toHaveAttribute('aria-disabled', 'true');
        expect(use).toHaveAccessibleDescription(/in use/i);
      }
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('M1 SR-1 opening the page sends no write, no screen-size check and no install; 60 s later still none', async () => {
    const page = await openEmulators();
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
    expect(requests(page, '/api/wizard/resolution')).toEqual([]);
    expect(region('emulatorsDetected')).toHaveTextContent('Main Tower');
    expect(region('emulatorsTarget').querySelector('[data-configured-serial]')).toHaveTextContent(
      'fake:1',
    );
    expect(region('emulatorsTools')).not.toHaveTextContent('Android platform tools');
    expect(await tipOf(page, region('emulatorsTools'), 'Connection tools')).toContain(
      'Android platform tools',
    );
    expect(
      within(region('emulatorsTarget')).getByRole('link', { name: 'Open Dashboard' }),
    ).toHaveAttribute('href', '#dashboard');
    expect(region('emulatorsTarget')).not.toHaveTextContent('pause harvesting');
    expect(await tipOf(page, region('emulatorsTarget'), 'Open Dashboard')).toContain(
      'Stop automation or pause harvesting on the Dashboard.',
    );
  });

  it.each([
    ['wizard_emulators.none', 'Nothing detected: is your emulator installed and running?'],
    ['wizard_emulators.scan_error', 'manager: Expecting value'],
  ])('M1 %s is said as it is', async (path, line) => {
    await openEmulators({ scan: () => v1(path) });
    expect(region('emulatorsDetected')).toHaveTextContent(line);
  });

  it('M1 a foreign adb is listed as not probed, with the scan reason; two running VMs offer no suggestion', async () => {
    await openEmulators({
      scan: () =>
        pages('emulator_inventory.two_running_vms_the_configured_one_is_down_no_automatic_choice'),
    });
    const box = region('emulatorsDetected');
    const notProbed = within(box).getByRole('region', { name: 'Not probed:' });
    expect(notProbed).toHaveTextContent('a foreign adb restarts the shared daemon');
    expect(within(notProbed).getByText(/a foreign adb restarts the shared daemon/u)).toBeVisible();
    expect(box.querySelector('[data-suggestion]')).toBeNull();
    expect(within(box).getAllByRole('button', { name: 'Use this one' })).toHaveLength(2);
  });

  it('M1 Scan again reads the discovery once more (a GET) and keeps a preview draft', async () => {
    const page = await openEmulators();
    const fps = region('emulatorsPreview').querySelector<HTMLInputElement>('[data-part="fps"]');
    if (fps === null) throw new Error('fps missing');
    await page.user.clear(fps);
    await page.user.type(fps, '20');
    const before = requests(page, '/api/wizard/emulators').length;
    await page.user.click(
      within(region('emulatorsDetected')).getByRole('button', { name: 'Scan again' }),
    );
    await page.advance(1_000);
    expect(requests(page, '/api/wizard/emulators').length).toBe(before + 1);
    expect(region('emulatorsPreview').querySelector('[data-part="fps"]')).toHaveValue('20');
    expect(page.fake.writes()).toEqual([]);
  });
});

describe('M2 using an emulator', () => {
  it('M2 SR-6 Use this one asks first with instance, account and both addresses; Cancel sends nothing; Use it sends the recorded body once', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const page = await openEmulators({ config: () => config() });
    const suggestion = region('emulatorsDetected').querySelector<HTMLElement>('[data-suggestion]');
    if (suggestion === null) throw new Error('no suggestion');
    await page.user.click(within(suggestion).getByRole('button', { name: 'Use this one' }));
    expect(dialog()).toHaveTextContent(
      'Instance main (account none chosen) will use 127.0.0.1:16384 instead of fake:1',
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(suggestion).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    const recorded = pages('emulator_actions.adopt_a_serial_saved_and_connected');
    expect(posts(page, '/api/wizard/adopt')).toEqual([recorded.request.body]);
    config = () => pages('emulator_actions.config_after_the_adopt_names_the_serial');
    page.fake.next('/api/wizard/adopt', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    const target = region('emulatorsTarget');
    expect(target.querySelector('[data-kind="adopt"]')).toHaveAttribute('data-outcome', 'saved');
    expect(target.querySelector('[data-connect]')).toHaveTextContent(
      'Its connect attempt: connected',
    );
    expect(page.data.store.getSnapshot().context?.serial).toBe('127.0.0.1:16384');
    expect(target.querySelector('[data-configured-serial]')).toHaveTextContent('127.0.0.1:16384');
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/adopt')).toHaveLength(1);
  });

  it('M2 SR-3 saved but not connected shows both facts and the server message', async () => {
    const page = await openEmulators();
    const box = row('MuMu:0');
    await page.user.click(within(box).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    const answer = pages('emulator_actions.adopt_saved_but_not_connected');
    page.fake.next('/api/wizard/adopt', 'POST').respond(answer.status, answer.body);
    await page.advance(1_000);
    const line = region('emulatorsTarget').querySelector('[data-kind="adopt"]');
    expect(line).toHaveTextContent('Saved as the address of this instance');
    expect(line).toHaveTextContent('Its connect attempt: not connected');
    expect(line).toHaveTextContent('cannot connect to 127.0.0.1:16384: refused');
  });

  it('M2 SR-7 a scan for another instance than the page shows refuses with the server reason, nothing resent', async () => {
    const page = await openEmulators({
      scan: () =>
        pages('emulator_inventory.two_running_vms_the_configured_one_is_down_no_automatic_choice'),
    });
    const box = row('MuMu:1');
    await page.user.click(within(box).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    expect(posts(page, '/api/wizard/adopt')).toEqual([
      {
        serial: '127.0.0.1:16416',
        adb: '<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe',
        expect_instance: 'main',
      },
    ]);
    const refused = pages('emulator_actions.adopt_the_instance_changed_since_the_scan');
    page.fake.next('/api/wizard/adopt', 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    const line = region('emulatorsTarget').querySelector('[data-kind="adopt"]');
    expect(line).toHaveAttribute('data-outcome', 'refused');
    expect(line).toHaveTextContent('active instance changed since the scan');
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/adopt')).toHaveLength(1);
  });

  it('M2 M3 M5 SR-7 while the status read lists a runner, Use, Start and Install say why and a click sends nothing', async () => {
    const page = await openEmulators(
      {},
      { status: () => v1('status.processes_two_rows_same_runner').body },
    );
    const reason = 'Automation is running; stop it on the Dashboard first';
    const use = within(row('MuMu:0')).getByRole('button', { name: 'Use this one' });
    const start = within(row('MuMu:3')).getByRole('button', { name: 'Start it' });
    await page.user.click(
      within(region('emulatorsTools')).getByRole('checkbox', { name: /Android SDK terms/u }),
    );
    const install = within(region('emulatorsTools')).getByRole('button', {
      name: 'Install connection tools',
    });
    for (const button of [use, start, install]) {
      expect(button).toHaveAttribute('aria-disabled', 'true');
      expect(button).toHaveAccessibleDescription(reason);
      await page.user.click(button);
    }
    await page.advance(0);
    expect(document.querySelector('dialog[open]')).toBeNull();
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('M2 a placeholder scan sends the fill-empty-only guard', async () => {
    const page = await openEmulators({
      scan: () => pages('emulator_inventory.no_serial_configured_yet_placeholder'),
    });
    const suggestion = region('emulatorsDetected').querySelector<HTMLElement>('[data-suggestion]');
    if (suggestion === null) throw new Error('no suggestion');
    await page.user.click(within(suggestion).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    expect(posts(page, '/api/wizard/adopt')).toEqual([
      {
        serial: '127.0.0.1:16384',
        adb: '<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe',
        expect_instance: 'main',
        expect_placeholder: true,
      },
    ]);
  });

  it('M2 SR-4 an adopt whose answer is lost is Unknown and never resent; the config read after the loss settles it', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const page = await openEmulators({ config: () => config() });
    await page.user.click(within(row('MuMu:0')).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    config = () => pages('emulator_actions.config_after_the_adopt_names_the_serial');
    page.fake.next('/api/wizard/adopt', 'POST').lose();
    await page.advance(1_000);
    expect(region('emulatorsTarget').querySelector('[data-kind="adopt"]')).toHaveAttribute(
      'data-outcome',
      'applied',
    );
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/adopt')).toHaveLength(1);
  });
});

describe('M2 an adopt the configuration does not show', () => {
  it('M2 SR-4 F6C-R1-02 a lost adopt and an unchanged config stay Unknown; Use waits for that read, then is a fresh click; nothing is resent', async () => {
    let config: () => DashboardCase | 'hold' = () => pages('config_state.read_before_the_save');
    const page = await openEmulators({ config: () => config() });
    const use = () => within(row('MuMu:0')).getByRole('button', { name: 'Use this one' });
    await page.user.click(use());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    config = () => 'hold';
    page.fake.next('/api/wizard/adopt', 'POST').lose();
    await page.advance(1_000);
    const line = () => region('emulatorsTarget').querySelector('[data-kind="adopt"]');
    expect(line()).toHaveAttribute('data-outcome', 'unknown');
    expect(use()).toHaveAttribute('aria-disabled', 'true');
    expect(use()).toHaveAccessibleDescription(
      'The last address change has no reply yet; waiting for the next configuration read',
    );
    // The config read started after the loss answers: the configuration did not move.
    const unchanged = pages('emulator_actions.config_after_the_failed_adopt_is_unchanged');
    config = () => unchanged;
    page.fake.next('/api/config', 'GET').respond(unchanged.status, unchanged.body);
    await page.advance(1_000);
    expect(line()).toHaveAttribute('data-outcome', 'unknown');
    expect(use()).not.toHaveAttribute('aria-disabled');
    await page.advance(60_000);
    expect(line()).toHaveAttribute('data-outcome', 'unknown');
    expect(posts(page, '/api/wizard/adopt')).toHaveLength(1);
  });
});

describe('M3 starting an emulator', () => {
  it('M3 SR-6 Start it sends the recorded body once; the accepted line says it is not a game screen; Start waits for a new scan', async () => {
    const page = await openEmulators();
    const box = () => row('MuMu:3');
    await page.user.dblClick(within(box()).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    expect(posts(page, '/api/wizard/launch')).toEqual([{ emulator: 'MuMu', index: 3 }]);
    const accepted = pages('emulator_actions.launch_a_vm_no_instance_uses_skips_the_boot_pipeline');
    page.fake.next('/api/wizard/launch', 'POST').respond(accepted.status, accepted.body);
    await page.advance(1_000);
    expect(box().querySelector('[data-kind="launch"]')).toHaveAttribute('data-outcome', 'accepted');
    expect(box()).toHaveTextContent('Start requested');
    const start = within(box()).getByRole('button', { name: 'Start it' });
    expect(start).toHaveAttribute('aria-disabled', 'true');
    expect(start).toHaveAccessibleDescription(
      'Start requested; scan again before starting it again',
    );
    await page.user.click(start);
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/launch')).toHaveLength(1);
    await page.user.click(
      within(region('emulatorsDetected')).getByRole('button', { name: 'Scan again' }),
    );
    await page.advance(1_000);
    expect(within(box()).getByRole('button', { name: 'Start it' })).not.toHaveAttribute(
      'aria-disabled',
    );
  });

  it('M3 an HTTP 200 with ok:false is the manager failing: Blocked with its message', async () => {
    const page = await openEmulators();
    await page.user.click(within(row('MuMu:3')).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    const failed = pages('emulator_actions.launch_the_manager_reported_a_failure');
    page.fake.next('/api/wizard/launch', 'POST').respond(failed.status, failed.body);
    await page.advance(1_000);
    const line = row('MuMu:3').querySelector('[data-kind="launch"]');
    expect(line).toHaveAttribute('data-outcome', 'failed');
    expect(line).toHaveTextContent('instance 9 not found');
  });

  it('M3 M8 SR-7 a refusal while a runner owns the device shows the shared owner notice with the process; nothing is resent', async () => {
    let holdStatus = false;
    const page = await openEmulators(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/status', () =>
            holdStatus ? 'hold' : { status: 200, body: statusBody(sample('status.off')) },
          );
        },
      },
    );
    await page.user.click(within(row('MuMu:3')).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    holdStatus = true;
    const refused = pages('emulator_actions.launch_refused_while_a_runner_owns_the_device');
    page.fake.next('/api/wizard/launch', 'POST').respond(refused.status, refused.body);
    await page.advance(1_000);
    // No status read after the refusal yet: the notice names the process the server sent.
    const notice = () => region('emulatorsTarget').querySelector('[data-owner-notice]');
    expect(notice()).toHaveTextContent('4632');
    expect(notice()).toHaveTextContent('orchestrator');
    expect(notice()).toHaveTextContent('runners alive - stop them first');
    expect(notice()).not.toHaveTextContent('The emulator is free');
    // A later read with no runner proves the emulator free; trying again stays a click.
    holdStatus = false;
    await page.advance(60_000);
    expect(notice()).toHaveTextContent('The emulator is free');
    expect(posts(page, '/api/wizard/launch')).toHaveLength(1);
  });

  it('M3 SR-4 a launch whose answer is lost is Unknown, has no Dismiss and is never resent', async () => {
    const page = await openEmulators();
    await page.user.click(within(row('MuMu:3')).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    page.fake.next('/api/wizard/launch', 'POST').lose();
    await page.advance(1_000);
    const line = row('MuMu:3').querySelector<HTMLElement>('[data-kind="launch"]');
    expect(line).toHaveAttribute('data-outcome', 'unknown');
    if (line === null) throw new Error('no line');
    expect(within(line).queryByRole('button', { name: 'Dismiss' })).toBeNull();
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/launch')).toHaveLength(1);
  });
});

describe('M5 connection tools', () => {
  it('M5 SR-6 Install waits for the license tick; one 202 starts it; the progress is followed until installed; nothing resent', async () => {
    let tools: () => DashboardCase = () => pages('emulator_tools.tools_idle_before_any_install');
    const page = await openEmulators({ tools: () => tools() });
    const box = () => region('emulatorsTools');
    const install = () => within(box()).getByRole('button', { name: 'Install connection tools' });
    expect(install()).toHaveAttribute('aria-disabled', 'true');
    expect(install()).toHaveAccessibleDescription('Accept the Android SDK terms first');
    await page.user.click(install());
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(box()).getByRole('checkbox', { name: /Android SDK terms/u }));
    await page.user.click(install());
    await page.advance(0);
    const recorded = pages('emulator_tools.install_accepted_202_the_download_starts');
    expect(posts(page, '/api/wizard/tools/install')).toEqual([recorded.request.body]);
    tools = () => pages('emulator_tools.tools_downloading_with_progress');
    page.fake.next('/api/wizard/tools/install', 'POST').respond(recorded.status, recorded.body);
    await page.advance(1_000);
    expect(box()).toHaveTextContent('Install started');
    expect(box()).toHaveTextContent('40 %');
    const reads = requests(page, '/api/wizard/tools').length;
    await page.advance(5_000);
    expect(requests(page, '/api/wizard/tools').length).toBeGreaterThan(reads);
    tools = () => pages('emulator_tools.tools_installed_after_the_install');
    await page.advance(3_000);
    expect(box().querySelector('[data-tools="installed"]')).not.toBeNull();
    expect(box()).toHaveTextContent('<root>/tools/platform-tools/adb.exe');
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/tools/install')).toHaveLength(1);
  });

  it('M5 leaving the page stops following the progress; it never claims the install was cancelled', async () => {
    const page = await openEmulators({
      tools: () => pages('emulator_tools.tools_downloading_with_progress'),
    });
    expect(region('emulatorsTools')).toHaveTextContent('it does not cancel the install');
    window.location.hash = '#dashboard';
    await page.advance(1_000);
    const reads = requests(page, '/api/wizard/tools').length;
    await page.advance(30_000);
    expect(requests(page, '/api/wizard/tools').length).toBe(reads);
    expect(page.fake.writes()).toEqual([]);
  });

  it('M5 an error state shows the server message and offers the install again', async () => {
    await openEmulators({
      tools: () => pages('emulator_tools.tools_error_state_after_a_failed_install'),
    });
    expect(region('emulatorsTools')).toHaveTextContent('the download was interrupted');
  });
});

describe('M6 screen size', () => {
  it.each([
    [
      'emulator_inventory.resolution_portrait_reference',
      '1080 x 2560',
      'Exactly the reference layout',
    ],
    [
      'emulator_inventory.resolution_portrait_not_the_reference_size',
      '1080 x 2400',
      'not the 1080 x 2560 reference size',
    ],
    ['emulator_inventory.resolution_landscape_is_not_portrait', '2560 x 1080', 'Not portrait'],
    [
      'emulator_inventory.resolution_no_adb_server',
      "The screen size couldn't be read",
      'adb server unreachable',
    ],
  ])(
    'M6 %s: only on a click, for the configured address; raw size and meaning',
    async (path, size, meaning) => {
      const page = await openEmulators({ resolution: () => pages(path) });
      expect(requests(page, '/api/wizard/resolution')).toEqual([]);
      await page.user.click(
        within(region('emulatorsTarget')).getByRole('button', { name: 'Check screen size' }),
      );
      await page.advance(1_000);
      expect(requests(page, '/api/wizard/resolution').map((request) => request.path)).toEqual([
        '/api/wizard/resolution?serial=fake%3A1',
      ]);
      const box = region('emulatorsTarget');
      expect(box).toHaveTextContent(size);
      expect(box).toHaveTextContent(meaning);
      expect(await tipOf(page, box, 'Screen size')).toContain('A size is not recognition');
      expect(page.fake.writes()).toEqual([]);
    },
  );
});

describe('M7 preview settings', () => {
  it('M7 SR-5 an invalid frame rate keeps the raw text with its reason; a valid change saves only the changed field', async () => {
    const page = await openEmulators();
    const box = () => region('emulatorsPreview');
    expect(box()).not.toHaveTextContent('not a live video');
    expect(await tipOf(page, box(), 'Video preview settings')).toContain(
      'Saved settings for the preview, not a live video',
    );
    const fps = () => {
      const found = box().querySelector<HTMLInputElement>('[data-part="fps"]');
      if (found === null) throw new Error('fps missing');
      return found;
    };
    await page.user.clear(fps());
    await page.user.type(fps(), '0.2');
    expect(fps()).toHaveValue('0.2');
    expect(fps()).toHaveAccessibleDescription('Below the lowest allowed value');
    await page.user.clear(fps());
    await page.user.type(fps(), '12.5');
    const save = [...box().querySelectorAll('button')].find(
      (button) => button.textContent === 'Save',
    );
    if (save === undefined) throw new Error('Save missing');
    await page.user.click(save);
    await page.advance(0);
    expect(postBodies(page.fake, '/api/settings')).toEqual([
      {
        context: { account: null, instance: 'main', profile: 'mine' },
        module: 'device-preview',
        revision: (pages('settings.populated_account_every_module').body as { revision: string })
          .revision,
        values: { fps: 12.5 },
      },
    ]);
  });
});

describe('M8 reconnect', () => {
  it('M8 an answer that the device is offline says so with the device state; nothing is resent', async () => {
    const page = await openEmulators();
    await page.user.click(
      within(region('emulatorsTarget')).getByRole('button', { name: 'Reconnect' }),
    );
    await page.advance(0);
    const offline = v1('reconnect.answered_offline_after_the_retries');
    page.fake.next('/api/wizard/reconnect', 'POST').respond(offline.status, offline.body);
    await page.advance(1_000);
    const line = region('emulatorsTarget').querySelector('[data-kind="reconnect"]');
    expect(line).toHaveTextContent('Not connected: the adb server reports');
    expect(line).toHaveTextContent('offline');
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/reconnect')).toHaveLength(1);
  });

  it('M1-M8 has no axe violations and only catalogue or server text', async () => {
    await openEmulators();
    vi.useRealTimers();
    expect(await axe(document.body)).toHaveNoViolations();
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });
});

describe('Emulators compliance review round 1 (F6M)', () => {
  const tools = () => region('emulatorsTools');
  const installButton = () =>
    within(tools()).getByRole('button', { name: 'Install connection tools' });
  const acceptLicense = async (page: Page) => {
    await page.user.click(within(tools()).getByRole('checkbox', { name: /Android SDK terms/u }));
  };
  /** config_state.read_before_the_save with the shipped first-install address: `serial: ""`. */
  const firstInstallConfig = (): DashboardCase => {
    const read = pages('config_state.read_before_the_save');
    const body = read.body as { instances: Record<string, object> };
    return {
      ...read,
      body: {
        ...body,
        instances: { ...body.instances, main: { ...body.instances['main'], serial: '' } },
      },
    };
  };

  it('F6M-01 M2 M3 M5 a first install (no address configured) can Use the placeholder suggestion, Start and install tools; each is one confirmed click', async () => {
    const page = await openEmulators({
      config: firstInstallConfig,
      scan: () => pages('emulator_inventory.no_serial_configured_yet_placeholder'),
    });
    expect(page.data.store.getSnapshot().context).toBeNull();
    expect(region('emulatorsTarget').querySelector('[data-unconfigured]')).toHaveTextContent(
      'No address is configured for this instance yet',
    );
    expect(region('emulatorsTarget').querySelector('[data-configured-serial]')).toHaveTextContent(
      'not set yet',
    );
    const suggestion = region('emulatorsDetected').querySelector<HTMLElement>('[data-suggestion]');
    if (suggestion === null) throw new Error('no suggestion');
    const use = within(suggestion).getByRole('button', { name: 'Use this one' });
    expect(use).not.toHaveAttribute('aria-disabled');
    await acceptLicense(page);
    expect(installButton()).not.toHaveAttribute('aria-disabled');
    await page.user.click(use);
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    expect(posts(page, '/api/wizard/adopt')).toEqual([
      {
        serial: '127.0.0.1:16384',
        adb: '<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe',
        expect_instance: 'main',
        expect_placeholder: true,
      },
    ]);
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/adopt')).toHaveLength(1);
  });

  it('F6M-02 M8 Reconnect sends the address shown, only while a click-time config read still names it', async () => {
    let config = () => pages('config_state.read_before_the_save');
    const page = await openEmulators({ config: () => config() });
    const reconnect = () =>
      within(region('emulatorsTarget')).getByRole('button', { name: 'Reconnect' });
    // Another tab repoints the instance; this page has not read it yet.
    config = () => pages('emulator_actions.config_after_the_adopt_names_the_serial');
    await page.user.click(reconnect());
    await page.advance(0);
    expect(posts(page, '/api/wizard/reconnect')).toEqual([]);
    expect(document.querySelector('[data-not-sent="context_changed"]')).not.toBeNull();
    // Back to what the page shows: the address shown is the one sent.
    config = () => pages('config_state.read_before_the_save');
    await page.advance(5_000);
    await page.user.click(reconnect());
    await page.advance(0);
    expect(posts(page, '/api/wizard/reconnect')).toEqual([{ serial: 'fake:1' }]);
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/reconnect')).toHaveLength(1);
  });

  it('F6M-03 one connection request at a time: a held Reconnect holds Use, Start and Install, and a held Start holds Reconnect', async () => {
    const page = await openEmulators();
    await acceptLicense(page);
    await page.user.click(
      within(region('emulatorsTarget')).getByRole('button', { name: 'Reconnect' }),
    );
    await page.advance(0);
    expect(posts(page, '/api/wizard/reconnect')).toHaveLength(1);
    const reason = 'Waiting for the reply to the last request';
    const use = within(row('MuMu:0')).getByRole('button', { name: 'Use this one' });
    const start = within(row('MuMu:3')).getByRole('button', { name: 'Start it' });
    for (const button of [use, start, installButton()]) {
      expect(button).toHaveAttribute('aria-disabled', 'true');
      expect(button).toHaveAccessibleDescription(reason);
      await page.user.click(button);
    }
    await page.advance(0);
    expect(page.fake.writes().map((write) => write.path)).toEqual(['/api/wizard/reconnect']);
    const offline = v1('reconnect.answered_offline_after_the_retries');
    page.fake.next('/api/wizard/reconnect', 'POST').respond(offline.status, offline.body);
    await page.advance(1_000);
    await page.user.click(within(row('MuMu:3')).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    const held = within(region('emulatorsTarget')).getByRole('button', { name: 'Reconnect' });
    expect(held).toHaveAttribute('aria-disabled', 'true');
    await page.user.click(held);
    await page.advance(10_000);
    expect(page.fake.writes().map((write) => write.path)).toEqual([
      '/api/wizard/reconnect',
      '/api/wizard/launch',
    ]);
  });

  it('F6M-04 M7 two preview saves in a row on this page: the second waits only for the settings read', async () => {
    let settings = () => pages('settings.populated_account_every_module');
    const page = await openEmulators(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/settings', () => answerOf(settings()));
        },
      },
    );
    const box = () => region('emulatorsPreview');
    const field = (key: string) => {
      const found = box().querySelector<HTMLInputElement>(`[data-part="${key}"]`);
      if (found === null) throw new Error(`${key} missing`);
      return found;
    };
    const save = () => {
      const found = [...box().querySelectorAll('button')].find((b) => b.textContent === 'Save');
      if (found === undefined) throw new Error('Save missing');
      return found;
    };
    await page.user.clear(field('fps'));
    await page.user.type(field('fps'), '12.5');
    await page.user.click(save());
    await page.advance(0);
    const saved = pages('settings.saved_device_preview');
    settings = () => saved;
    page.fake.next('/api/settings', 'POST').respond(saved.status, saved.body);
    await page.advance(5_000);
    await page.user.clear(field('scrcpy.bitrate'));
    await page.user.type(field('scrcpy.bitrate'), '9000000');
    expect(save()).not.toHaveAttribute('aria-disabled');
    await page.user.click(save());
    await page.advance(0);
    expect(postBodies(page.fake, '/api/settings')).toHaveLength(2);
    expect(postBodies(page.fake, '/api/settings')[1]).toMatchObject({
      module: 'device-preview',
      values: { 'scrcpy.bitrate': 9000000 },
    });
  });

  it.each([
    ['hold', 'No scan read yet'],
    ['other', 'Scan again: the last scan was for another instance'],
  ] as const)('F6M-05 M5 Install waits for the scan (%s): %s', async (kind, reason) => {
    // 'other': read_before_the_save with active_instance other (fake:2), while every recorded
    // scan names main.
    const read = pages('config_state.read_before_the_save');
    const page = await openEmulators({
      scan: () =>
        kind === 'hold' ? 'hold' : v1('wizard_emulators.instances_with_names_and_ports'),
      config: () =>
        kind === 'hold'
          ? read
          : { ...read, body: { ...(read.body as object), active_instance: 'other' } },
    });
    await acceptLicense(page);
    expect(installButton()).toHaveAttribute('aria-disabled', 'true');
    expect(installButton()).toHaveAccessibleDescription(reason);
    await page.user.click(installButton());
    await page.advance(10_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('F6M-06 M1 M5 unread tools, a manager-error row and a failed status read are not stated as facts', async () => {
    let status: () => unknown = () => {
      const base = v1('status.processes_empty').body as { processes: unknown[] };
      // A recorded status read with one boot runner row added (no recorded read lists one).
      return {
        ...base,
        processes: [
          {
            runner: 'boot',
            pid: 4700,
            created: 1799990001.25,
            cmdline: 'C:/Python/pythonw.exe -m runtime.boot --instance main',
          },
        ],
      };
    };
    const page = await openEmulators(
      { tools: () => 'hold', scan: () => v1('wizard_emulators.scan_error') },
      { status: () => status() },
    );
    expect(tools().querySelector('[data-tools]')).toHaveTextContent('Not read yet');
    expect(tools()).not.toHaveTextContent('Not installed');
    expect(region('emulatorsDetected')).toHaveTextContent('state not reported');
    expect(region('emulatorsDetected')).not.toHaveTextContent('listed by adb');
    expect(region('emulatorsTarget').querySelector('[data-boot]')).toHaveTextContent(
      'The last status read lists a boot runner',
    );
    status = () => {
      throw new Error('no answer');
    };
    page.fake.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
    await page.advance(10_000);
    expect(region('emulatorsTarget').querySelector('[data-boot]')).toBeNull();
  });

  it.each([
    ['lost', 'The screen size check got no answer'],
    ['text', "The screen size answer couldn't be read"],
  ] as const)('F6M-07 M6 a screen-size check that %s says so', async (kind, line) => {
    const page = await openEmulators(
      {},
      {
        before: (fake) => {
          fake.route('GET', /^\/api\/wizard\/resolution/u, () =>
            kind === 'lost' ? 'hold' : { status: 500, body: 'Internal Server Error' },
          );
        },
      },
    );
    await page.user.click(
      within(region('emulatorsTarget')).getByRole('button', { name: 'Check screen size' }),
    );
    if (kind === 'lost') page.fake.next(/^\/api\/wizard\/resolution/u, 'GET').lose();
    await page.advance(1_000);
    const box = region('emulatorsTarget').querySelector('[data-resolution="no_result"]');
    expect(box).toHaveTextContent(line);
    if (kind === 'text') expect(box).toHaveTextContent('HTTP 500');
    expect(page.fake.writes()).toEqual([]);
  });

  it.each([
    ['hold', 'The saved settings are not read yet'],
    ['other', 'The last settings read was for another emulator, account or profile'],
    ['none', 'The saved settings have no video preview part'],
  ] as const)('F6M-08 M7 preview settings %s: %s, with a read-only refresh', async (kind, line) => {
    const read = pages('settings.populated_account_every_module');
    const body = read.body as { modules: { id: string }[] };
    const page = await openEmulators(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/settings', () =>
            kind === 'hold'
              ? 'hold'
              : kind === 'other'
                ? answerOf(pages('settings.read_after_the_switch_names_the_new_emulator'))
                : // The recorded read of this context without its device-preview module.
                  {
                    status: 200,
                    body: {
                      ...body,
                      modules: body.modules.filter((m) => m.id !== 'device-preview'),
                    },
                  },
          );
        },
      },
    );
    const box = region('emulatorsPreview');
    expect(box.querySelector('[data-preview-state]')).toHaveTextContent(line);
    expect(box.querySelector('[data-part]')).toBeNull();
    const reads = requests(page, '/api/settings').length;
    await page.user.click(
      within(box).getByRole('button', { name: 'Read the saved settings again' }),
    );
    await page.advance(1_000);
    // A held read is still in flight: the refresh joins it (one read at a time).
    if (kind !== 'hold') expect(requests(page, '/api/settings').length).toBeGreaterThan(reads);
    expect(page.fake.writes()).toEqual([]);
  });

  it('F6M-09 M4 a 5xx naming its error keeps Unknown and shows the server words; nothing is resent', async () => {
    const page = await openEmulators();
    await page.user.click(within(row('MuMu:3')).getByRole('button', { name: 'Start it' }));
    await page.advance(0);
    const failed = pages('emulator_actions.launch_the_manager_could_not_be_started_500');
    page.fake.next('/api/wizard/launch', 'POST').respond(failed.status, failed.body);
    await page.advance(1_000);
    const line = row('MuMu:3').querySelector('[data-kind="launch"]');
    expect(line).toHaveAttribute('data-outcome', 'unknown');
    expect(line?.querySelector('[data-server-error]')).toHaveTextContent(
      'The server reported:MuMuManager.exe could not be started',
    );
    await page.advance(60_000);
    expect(posts(page, '/api/wizard/launch')).toHaveLength(1);
  });

  it('F6M-10 M7 SR-5 a saved preview source that is not one of its options is shown raw with its reason and never sent by itself', async () => {
    const read = pages('settings.populated_account_every_module');
    const body = read.body as { modules: { id: string; fields: { path: string }[] }[] };
    // The recorded read with device-preview.backend set to a value outside its options.
    const bogus = {
      ...body,
      modules: body.modules.map((module) =>
        module.id !== 'device-preview'
          ? module
          : {
              ...module,
              fields: module.fields.map((field) =>
                field.path === 'backend' ? { ...field, value: 'bogus' } : field,
              ),
            },
      ),
    };
    const page = await openEmulators(
      {},
      {
        before: (fake) => {
          fake.route('GET', '/api/settings', () => ({ status: 200, body: bogus }));
        },
      },
    );
    const select = region('emulatorsPreview').querySelector<HTMLSelectElement>(
      '[data-field="backend"] select',
    );
    if (select === null) throw new Error('backend select missing');
    expect(select).toHaveValue('bogus');
    expect(select).toHaveAttribute('aria-invalid', 'true');
    expect(within(select).getByRole('option', { name: 'bogus' })).toBeInTheDocument();
    const fps = region('emulatorsPreview').querySelector<HTMLInputElement>('[data-part="fps"]');
    if (fps === null) throw new Error('fps missing');
    await page.user.clear(fps);
    await page.user.type(fps, '20');
    const save = [...region('emulatorsPreview').querySelectorAll('button')].find(
      (button) => button.textContent === 'Save',
    );
    if (save === undefined) throw new Error('Save missing');
    await page.user.click(save);
    await page.advance(0);
    expect(postBodies(page.fake, '/api/settings')).toEqual([
      expect.objectContaining({ module: 'device-preview', values: { fps: 20 } }),
    ]);
  });
});

describe('Emulators confirmation round (F6M-C)', () => {
  const runnerStatus = () => v1('status.processes_two_rows_same_runner').body;
  const idleStatus = () => statusBody(sample('status.off'));

  it('F6M-C-01 M2 a first-install Use confirmation names the instance and account it sends for, and both addresses; Cancel sends nothing', async () => {
    const read = pages('config_state.read_before_the_save');
    const body = read.body as { instances: Record<string, object> };
    // read_before_the_save with the shipped first-install address (no recorded case has one).
    const firstInstall: DashboardCase = {
      ...read,
      body: {
        ...body,
        instances: { ...body.instances, main: { ...body.instances['main'], serial: '' } },
      },
    };
    const page = await openEmulators({
      config: () => firstInstall,
      scan: () => pages('emulator_inventory.no_serial_configured_yet_placeholder'),
    });
    const suggestion = region('emulatorsDetected').querySelector<HTMLElement>('[data-suggestion]');
    if (suggestion === null) throw new Error('no suggestion');
    await page.user.click(within(suggestion).getByRole('button', { name: 'Use this one' }));
    expect(dialog()).toHaveTextContent(
      'Instance main (account none chosen) will use 127.0.0.1:16384 instead of not set yet',
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(within(suggestion).getByRole('button', { name: 'Use this one' }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use it' }));
    await page.advance(0);
    expect(posts(page, '/api/wizard/adopt')).toEqual([
      expect.objectContaining({ serial: '127.0.0.1:16384', expect_instance: 'main' }),
    ]);
  });

  it('F6M-C-02 M2 a Use confirmation left open closes when a runner is listed; nothing is sent', async () => {
    let status: () => unknown = idleStatus;
    const page = await openEmulators({}, { status: () => status() });
    await page.user.click(within(row('MuMu:0')).getByRole('button', { name: 'Use this one' }));
    expect(document.querySelector('dialog[open]')).not.toBeNull();
    status = runnerStatus;
    await page.advance(5_000);
    expect(document.querySelector('dialog[open]')).toBeNull();
    expect(within(row('MuMu:0')).getByRole('button', { name: 'Use this one' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('F6M-C-02 M8 a runner listed while the Reconnect click-time read is in flight stops the send', async () => {
    let status: () => unknown = idleStatus;
    let holdConfig = false;
    const page = await openEmulators(
      { config: () => (holdConfig ? 'hold' : pages('config_state.read_before_the_save')) },
      { status: () => status() },
    );
    holdConfig = true;
    await page.user.click(
      within(region('emulatorsTarget')).getByRole('button', { name: 'Reconnect' }),
    );
    await page.advance(0);
    status = runnerStatus;
    await page.advance(3_000);
    const held = page.fake.held.find(
      (entry) => entry.request.path === '/api/config' && !entry.settled(),
    );
    if (held === undefined) throw new Error('no held config read');
    const config = answerOf(pages('config_state.read_before_the_save'));
    held.respond(config.status, config.body);
    await page.advance(1_000);
    expect(posts(page, '/api/wizard/reconnect')).toEqual([]);
    expect(document.querySelector('[data-not-sent="process_listed"]')).not.toBeNull();
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
  });
});

describe('M4 BlueStacks confirmation round from r11 recorded cases', () => {
  it('M4 a stopped instance cannot Prepare while another BlueStacks instance runs; one global ADB warning and no POST', async () => {
    const page = await openEmulators({ scan: () => blueStacksMixed });
    const prepare = within(row('BlueStacks:Pie64')).getByRole('button', {
      name: 'Prepare',
    });
    expect(prepare).toHaveAttribute('aria-disabled', 'true');
    expect(prepare).toHaveAccessibleDescription('Close every BlueStacks window first');
    expect(region('emulatorsDetected').querySelectorAll('[data-bluestacks-adb]')).toHaveLength(1);
    expect(region('emulatorsDetected')).toHaveTextContent('BlueStacks ADB: off for every instance');
    await page.user.click(prepare);
    await page.advance(60_000);
    expect(document.querySelector('dialog[open]')).toBeNull();
    expect(page.fake.writes()).toEqual([]);
  });

  it('M4 modified all-stopped inventory: consent names global ADB and selected display/file effects; Cancel sends nothing; one Prepare and recorded readback', async () => {
    let scan: DashboardCase = stoppedBlueStacks();
    const page = await openEmulators({ scan: () => scan });
    const prepare = () => within(row('BlueStacks:Pie64')).getByRole('button', { name: 'Prepare' });
    expect(prepare()).not.toHaveAttribute('aria-disabled');
    await page.user.click(prepare());
    const consent = dialog();
    for (const effect of [
      'bluestacks.conf',
      'Android Debug Bridge on for every BlueStacks instance',
      "selected instance's display 2560 x 1080 landscape at 360 dpi",
      'custom resolution selected',
      'A backup of the file is kept',
      'Every BlueStacks window must be closed first',
    ]) {
      expect(consent).toHaveTextContent(effect);
    }
    expect(textOutsideCatalogue(consent)).toEqual([]);
    await page.user.click(within(consent).getByRole('button', { name: 'Cancel' }));
    await page.advance(0);
    expect(page.fake.writes()).toEqual([]);
    await page.user.click(prepare());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Write bluestacks.conf' }));
    await page.advance(0);
    const answer = blueStacksCase('prepare_the_stopped_instance_once_nothing_runs');
    expect(posts(page, answer.request.path)).toEqual([answer.request.body]);
    page.fake.next(answer.request.path, 'POST').respond(answer.status, answer.body);
    await page.advance(1_000);
    const outcome = row('BlueStacks:Pie64').querySelector('[data-kind="prepare"]');
    expect(outcome).toHaveAttribute('data-outcome', 'done');
    expect(outcome).toHaveTextContent(answer.body.message);
    expect(outcome).toHaveTextContent(answer.body.backup);
    scan = blueStacksCase('read_after_the_prepare_display_ok_and_adb_on');
    await page.user.click(
      within(region('emulatorsDetected')).getByRole('button', { name: 'Scan again' }),
    );
    await page.advance(1_000);
    expect(region('emulatorsDetected')).toHaveTextContent('BlueStacks ADB: on for every instance');
    expect(within(row('BlueStacks:Pie64')).queryByRole('button', { name: 'Prepare' })).toBeNull();
    expect(region('emulatorsDetected')).not.toHaveTextContent('display not ready:');
    await page.advance(60_000);
    expect(posts(page, answer.request.path)).toEqual([answer.request.body]);
  });

  it('F6M-C-02 M4 modified all-stopped inventory: an open Prepare closes when a runner is listed and sends nothing', async () => {
    let status = v1('status.processes_empty').body;
    const page = await openEmulators({ scan: stoppedBlueStacks }, { status: () => status });
    await page.user.click(within(row('BlueStacks:Pie64')).getByRole('button', { name: 'Prepare' }));
    expect(dialog()).toBeVisible();
    status = v1('status.processes_two_rows_same_runner').body;
    await page.advance(5_000);
    expect(document.querySelector('dialog[open]')).toBeNull();
    const prepare = within(row('BlueStacks:Pie64')).getByRole('button', {
      name: 'Prepare',
    });
    expect(prepare).toHaveAttribute('aria-disabled', 'true');
    expect(prepare).toHaveAccessibleDescription(
      'Automation is running; stop it on the Dashboard first',
    );
    await page.advance(60_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('M4 modified all-stopped inventory: Prepare consent has no axe violations and only catalogue text', async () => {
    const page = await openEmulators({ scan: stoppedBlueStacks });
    await page.user.click(within(row('BlueStacks:Pie64')).getByRole('button', { name: 'Prepare' }));
    vi.useRealTimers();
    expect(await axe(dialog())).toHaveNoViolations();
    expect(textOutsideCatalogue(dialog())).toEqual([]);
  });
});
