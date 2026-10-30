import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer, type EndpointId } from '../../../src/api/wire.ts';
import {
  adoptAnswer,
  adoptBody,
  familyAdb,
  installAnswer,
  instanceActions,
  inventoryView,
  launchAnswer,
  launchBody,
  prepareAnswer,
  reconnectAnswer,
  resolutionView,
  settleAdopt,
  settleInstall,
  toolsView,
  type DetectedInstance,
} from '../../../src/core/emulators.ts';
import {
  dashboardCase,
  dashboardCaseNames,
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';

/**
 * The Emulators page's rules (emulators brief M1 to M8) against every answer the backend
 * recorded for them (pages fixture families emulator_actions, emulator_inventory,
 * emulator_tools; Dashboard fixture families wizard_emulators, wizard_tools, reconnect),
 * each parsed at the app's wire boundary first, as the page receives it.
 */
function wire(endpoint: EndpointId, found: DashboardCase) {
  const body = found.body as Record<string, unknown>;
  const text =
    Object.keys(body).length === 1 && typeof body['text'] === 'string'
      ? body['text']
      : JSON.stringify(body);
  return parseAnswer(ENDPOINTS[endpoint], found.status, text);
}

function endpointOf(found: DashboardCase): EndpointId | null {
  const { method, path } = found.request;
  if (method === 'GET') {
    if (path === '/api/config') return 'config';
    if (path === '/api/wizard/state') return 'wizardState';
    if (path === '/api/wizard/emulators') return 'wizardEmulators';
    if (path.startsWith('/api/wizard/resolution')) return 'resolution';
    if (path === '/api/wizard/tools') return 'wizardTools';
    return null;
  }
  if (path === '/api/wizard/adopt') return 'adopt';
  if (path === '/api/wizard/launch') return 'launch';
  if (path === '/api/wizard/bluestacks/prepare') return 'prepare';
  if (path === '/api/wizard/tools/install') return 'toolsInstall';
  if (path === '/api/wizard/reconnect') return 'reconnect';
  return null;
}

const PAGES = ['emulator_actions', 'emulator_inventory', 'emulator_tools'].flatMap((family) =>
  dashboardPagesCaseNames(family).map((name) => `${family}.${name}`),
);
const V1 = ['wizard_emulators', 'wizard_tools', 'wizard_state', 'reconnect'].flatMap((family) =>
  dashboardCaseNames(family).map((name) => `${family}.${name}`),
);

const pagesCases = PAGES.flatMap((path) => {
  const endpoint = endpointOf(dashboardPagesCase(path));
  return endpoint === null ? [] : [[path, endpoint] as const];
});
const v1Cases = V1.flatMap((path) => {
  const endpoint = endpointOf(dashboardCase(path));
  return endpoint === null ? [] : [[path, endpoint] as const];
});

describe('the Emulators contract at the wire boundary', () => {
  it.each(dashboardPagesCaseNames('emulator_bluestacks_inventory'))(
    'M4 emulator_bluestacks_inventory.%s keeps every recorded member',
    (name) => {
      const found = dashboardPagesCase(`emulator_bluestacks_inventory.${name}`);
      const endpoint = endpointOf(found);
      if (endpoint === null) throw new Error('BlueStacks case has no endpoint');
      const parsed = wire(endpoint, found);
      expect(parsed.contract).toBe('exact');
      expect(parsed.issues).toEqual([]);
      expect(parsed.body).toEqual(found.body);
    },
  );
  it.each(pagesCases)('M1-M8 %s is an exact %s answer and keeps every member', (path, endpoint) => {
    const parsed = wire(endpoint, dashboardPagesCase(path));
    expect(parsed.contract).toBe('exact');
    expect(parsed.issues).toEqual([]);
  });
  it.each(v1Cases)('M1 M5 M8 %s is an exact %s answer', (path, endpoint) => {
    const parsed = wire(endpoint, dashboardCase(path));
    expect(parsed.contract).toBe('exact');
    expect(parsed.issues).toEqual([]);
  });
});

const posts = (prefix: string) =>
  pagesCases.filter(([path]) => {
    const found = dashboardPagesCase(path);
    return found.request.method === 'POST' && found.request.path === prefix;
  });

// A 4xx is "not done" whatever its body (a JSON refusal, or the HTML page Flask sends for a
// body that is not JSON, before the handler runs); a 5xx or an unreadable 200 is unknown.
describe('M2 M3 M4 M5 every recorded request answer is classified by its status and body', () => {
  it.each(posts('/api/wizard/adopt'))('M2 SR-4 %s', (path) => {
    const found = dashboardPagesCase(path);
    const body = found.request.body as { serial?: unknown };
    const serial =
      typeof body === 'object' && typeof body.serial === 'string' ? body.serial : undefined;
    const answer = adoptAnswer(wire('adopt', found), { serial });
    if (found.status >= 500) expect(answer).toEqual({ kind: 'unknown' });
    else if (found.status >= 400) expect(answer.kind).toBe('refused');
    else expect(answer.kind).toBe('saved');
  });

  it.each(posts('/api/wizard/launch'))('M3 SR-4 %s', (path) => {
    const found = dashboardPagesCase(path);
    const answer = launchAnswer(wire('launch', found));
    const body = found.body as { ok?: unknown; text?: unknown };
    if (found.status >= 500) expect(answer).toEqual({ kind: 'unknown' });
    else if (found.status === 200)
      expect(answer.kind).toBe(body.ok === true ? 'accepted' : 'failed');
    else expect(answer.kind).toBe('refused');
  });

  it.each(posts('/api/wizard/bluestacks/prepare'))('M4 SR-4 %s', (path) => {
    const found = dashboardPagesCase(path);
    const answer = prepareAnswer(wire('prepare', found));
    if (found.status === 200) expect(answer.kind).toBe('done');
    else expect(answer.kind).toBe('refused');
  });

  it.each(posts('/api/wizard/tools/install'))('M5 SR-4 %s', (path) => {
    const found = dashboardPagesCase(path);
    const answer = installAnswer(wire('toolsInstall', found));
    if (found.status === 202 || found.status === 200) expect(answer.kind).toBe('accepted');
    else expect(answer.kind).toBe('refused');
  });

  it('M2 saved but not connected keeps both facts: saved, and its own connect attempt failed', () => {
    const found = dashboardPagesCase('emulator_actions.adopt_saved_but_not_connected');
    expect(adoptAnswer(wire('adopt', found), { serial: '127.0.0.1:16384' })).toMatchObject({
      kind: 'saved',
      connected: false,
      message: 'cannot connect to 127.0.0.1:16384: refused',
    });
  });

  it('M2 a 200 naming another address than the one sent is no receipt: Unknown', () => {
    const found = dashboardPagesCase('emulator_actions.adopt_a_serial_saved_and_connected');
    expect(adoptAnswer(wire('adopt', found), { serial: 'emulator-5554' })).toEqual({
      kind: 'unknown',
    });
  });

  it('M3 a 200 with ok:false is the manager failing, not a start', () => {
    const found = dashboardPagesCase('emulator_actions.launch_the_manager_reported_a_failure');
    expect(launchAnswer(wire('launch', found))).toEqual({
      kind: 'failed',
      message: 'instance 9 not found',
    });
  });

  it('M4 a prepare names every changed key with from and to, the added key and the backup; a second one changes nothing', () => {
    const done = prepareAnswer(
      wire(
        'prepare',
        dashboardPagesCase('emulator_actions.prepare_changes_the_adb_switch_and_the_framebuffer'),
      ),
    );
    expect(done).toMatchObject({
      kind: 'done',
      added: ['bst.instance.Pie64.custom_resolution_selected'],
      backup: '<root>/program-data/BlueStacks_nxt/bluestacks.conf.bak-20270115_080000',
    });
    if (done.kind === 'done')
      expect(done.changed).toContainEqual({ key: 'bst.enable_adb_access', from: '0', to: '1' });
    expect(
      prepareAnswer(
        wire('prepare', dashboardPagesCase('emulator_actions.prepare_again_changes_nothing')),
      ),
    ).toMatchObject({ kind: 'done', changed: [], added: [], backup: null });
  });

  it('M5 a 202 started the download; a 200 while downloading was answered, not restarted', () => {
    expect(
      installAnswer(
        wire(
          'toolsInstall',
          dashboardPagesCase('emulator_tools.install_accepted_202_the_download_starts'),
        ),
      ),
    ).toEqual({ kind: 'accepted', started: true, stage: 'downloading' });
    expect(
      installAnswer(
        wire(
          'toolsInstall',
          dashboardPagesCase(
            'emulator_tools.install_again_while_downloading_is_answered_not_restarted',
          ),
        ),
      ),
    ).toEqual({ kind: 'accepted', started: false, stage: 'downloading' });
  });

  it.each([
    ['reconnect.accepted_device_state_device', 'answered', true, 'device'],
    ['reconnect.answered_offline_after_the_retries', 'answered', false, 'offline'],
    ['reconnect.answered_with_another_device_state', 'answered', false, 'unauthorized'],
  ] as const)(
    'M8 %s: online only when the adb server lists the address as a device',
    (path, kind, online, state) => {
      expect(reconnectAnswer(wire('reconnect', dashboardCase(path)))).toMatchObject({
        kind,
        online,
        deviceState: state,
      });
    },
  );

  it('M8 the owner refusal and the connect exception stay a refusal and an unknown', () => {
    expect(
      reconnectAnswer(
        wire('reconnect', dashboardCase('reconnect.refused_while_a_runner_is_listed')),
      ).kind,
    ).toBe('refused');
    expect(
      reconnectAnswer(wire('reconnect', dashboardCase('reconnect.adb_connect_raised'))),
    ).toEqual({ kind: 'unknown' });
  });
});

describe('M2 the adopt request and its settlement by a later config read', () => {
  it('M2 the body carries the scan guards: the recorded request exactly', () => {
    const recorded = dashboardPagesCase('emulator_actions.adopt_a_serial_saved_and_connected');
    expect(
      adoptBody({
        serial: '127.0.0.1:16384',
        adb: '<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe',
        expectInstance: 'main',
        expectPlaceholder: false,
      }),
    ).toEqual(recorded.request.body);
    expect(
      adoptBody({
        serial: '127.0.0.1:16416',
        adb: null,
        expectInstance: 'main',
        expectPlaceholder: true,
      }),
    ).toEqual({ serial: '127.0.0.1:16416', expect_instance: 'main', expect_placeholder: true });
  });

  it.each([
    ['emulator_actions.config_after_the_adopt_names_the_serial', 'applied'],
    ['emulator_actions.config_after_the_failed_adopt_is_unchanged', null],
  ] as const)('M2 SR-4 a lost adopt read by %s is %s', (path, expected) => {
    const body = wire('config', dashboardPagesCase(path)).body;
    expect(settleAdopt(body, { instance: 'main', serial: '127.0.0.1:16384' })).toBe(expected);
  });

  it('M2 SR-4 F6C-R1-02 a config still at the revision the adopt was made from proves nothing: unknown', () => {
    const body = wire(
      'config',
      dashboardPagesCase('emulator_actions.config_after_the_failed_adopt_is_unchanged'),
    ).body as { config_revision: string };
    expect(settleAdopt(body, { instance: 'main', serial: '127.0.0.1:16384' })).toBeNull();
  });
});

describe('M1 M2 M3 M4 discovery, its suggestion and what each detected emulator offers', () => {
  const inventory = (path: string, v1 = false) => {
    const found = v1 ? dashboardCase(path) : dashboardPagesCase(path);
    const view = inventoryView(wire('wizardEmulators', found).body);
    if (view === null) throw new Error(`${path} unreadable`);
    return view;
  };

  it('M1 M2 two running VMs and the configured one down: no suggestion, each running VM offers Use', () => {
    const view = inventory(
      'emulator_inventory.two_running_vms_the_configured_one_is_down_no_automatic_choice',
    );
    expect(view.suggestion).toBeNull();
    expect(view.instances.map((row) => instanceActions(view, row).use)).toEqual([true, true]);
  });

  it('M1 a foreign adb is reported as not probed, with its reason', () => {
    const view = inventory(
      'emulator_inventory.configured_serial_differs_from_the_live_vm_one_candidate_is_suggested',
    );
    expect(view.scanned.find((tool) => tool.note !== null)?.note).toMatch(/not probed/u);
    expect(familyAdb(view, 'MuMu')).toBe('<root>/program-files/Netease/MuMuPlayer/nx_main/adb.exe');
  });

  it('M1 the scan error and the per-instance manager error stay distinct', () => {
    const view = inventory('wizard_emulators.scan_error', true);
    expect(view.instances[0]?.error).toMatch(/manager/u);
    expect(inventory('wizard_emulators.none', true).instances).toEqual([]);
  });

  it('M2 a placeholder scan marks the adopt as filling an empty address only', () => {
    const view = inventory('emulator_inventory.no_serial_configured_yet_placeholder');
    expect(view.placeholder).toBe(true);
    expect(view.configured.find((row) => row.instance === 'main')).toEqual({
      instance: 'main',
      serial: null,
      portOpen: null,
    });
  });

  it('M3 a running VM whose address is configured opens the game; a stopped one starts; the configured one is In use', () => {
    const view = inventory('emulator_inventory.configured_device_is_up_nothing_is_suggested');
    const [main, stopped] = view.instances;
    if (main === undefined || stopped === undefined) throw new Error('rows missing');
    expect(instanceActions(view, main)).toMatchObject({
      inUse: true,
      use: false,
      openGame: true,
      start: false,
    });
    expect(instanceActions(view, stopped)).toMatchObject({
      start: true,
      openGame: false,
      prepare: false,
    });
    expect(launchBody('MuMu', stopped.index ?? '')).toEqual({ emulator: 'MuMu', index: 3 });
  });

  it('M4 Prepare is offered only for a stopped BlueStacks instance whose ADB switch is off or display is not ready (rule on domain values)', () => {
    const view = inventory('wizard_emulators.none', true);
    const row = (over: Partial<DetectedInstance>): DetectedInstance => ({
      key: 'BlueStacks:Pie64',
      emulator: 'BlueStacks',
      index: 'Pie64',
      name: 'Pie64',
      running: false,
      serial: '127.0.0.1:5555',
      adbEnabled: true,
      display: null,
      displayOk: true,
      error: null,
      ...over,
    });
    expect(instanceActions(view, row({ adbEnabled: false })).prepare).toBe(true);
    expect(instanceActions(view, row({ displayOk: false })).prepare).toBe(true);
    expect(instanceActions(view, row({})).prepare).toBe(false);
    expect(instanceActions(view, row({ adbEnabled: false, running: true })).prepare).toBe(false);
    expect(launchBody('BlueStacks', 'Pie64')).toEqual({ emulator: 'BlueStacks', index: 'Pie64' });
    expect(launchBody('LDPlayer', '0')).toBeNull();
  });
});

describe('M5 M6 the tools state and the screen size', () => {
  const tools = (path: string) => wire('wizardTools', dashboardPagesCase(path)).body;
  it.each([
    // F6C-R1-02: idle cannot exclude a lost install that has not started its download yet.
    ['emulator_tools.tools_idle_before_any_install', false, false, null],
    ['emulator_tools.tools_downloading_with_progress', false, true, 'applied'],
    ['emulator_tools.tools_error_state_after_a_failed_install', false, false, null],
    ['emulator_tools.tools_installed_after_the_install', true, false, 'applied'],
  ] as const)(
    'M5 %s: installed %s, running %s; a lost install read by it is %s',
    (path, installed, running, settled) => {
      expect(toolsView(tools(path))).toMatchObject({ installed, inProgress: running });
      expect(settleInstall(tools(path))).toBe(settled);
    },
  );

  it.each([
    [
      'emulator_inventory.resolution_portrait_reference',
      { kind: 'measured', width: 1080, height: 2560, portrait: true, reference: true },
    ],
    [
      'emulator_inventory.resolution_portrait_not_the_reference_size',
      { kind: 'measured', width: 1080, height: 2400, portrait: true, reference: false },
    ],
    [
      'emulator_inventory.resolution_landscape_is_not_portrait',
      { kind: 'measured', width: 2560, height: 1080, portrait: false, reference: false },
    ],
    ['emulator_inventory.resolution_no_adb_server', { kind: 'failed' }],
    ['emulator_inventory.resolution_the_device_answered_text_not_a_frame', { kind: 'failed' }],
  ] as const)('M6 %s shows the raw size and its meaning', (path, expected) => {
    expect(resolutionView(wire('resolution', dashboardPagesCase(path)).body)).toMatchObject(
      expected,
    );
  });
});
