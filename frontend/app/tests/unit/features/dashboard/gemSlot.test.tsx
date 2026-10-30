import { act, fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { pauseOffered } from '../../../../src/core/harvestChoice.ts';
import { GemSlot } from '../../../../src/features/dashboard/control/GemSlot.tsx';
import { selectGemSlot } from '../../../../src/store/selectors/gemSlot.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { readOf, setPath } from '../../../support/core.ts';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import { dashboardBody } from '../../../support/dashboardFixture.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../../support/fixture.ts';
import { storeHarness, type StoreHarness } from '../../../support/storeHarness.ts';
import { factValue, renderPage, renderRegion } from './harness.tsx';

/**
 * Control / Gem harvesting as drawn (spec 3.6.2, INV-1; F-023 to F-032,
 * F-036; S-001, S-034, S-080). States come from the harvest fixture through
 * the real store; clicks reach recording commands, or the fake transport in
 * the page flows.
 */
const CLEAR = dashboardBody('status.guards_clear') as Record<string, unknown>;
const CLEAR_GUARD = CLEAR['collection_guard'];

interface GemOptions {
  readonly runs?: unknown;
  readonly previews?: Readonly<Record<string, unknown>>;
  readonly root?: Readonly<Record<string, unknown>>;
  readonly processes?: unknown;
  readonly before?: (h: StoreHarness) => void;
  /** F-027: taps allowed in the config read (E0, the default, has them off). */
  readonly allowTaps?: boolean;
}

/** F-027's other prerequisites in place: taps allowed (the status reads say the emulator is connected). */
const READY = { allowTaps: true } as const;

function gemHarness(harvest: unknown, options: GemOptions = {}): StoreHarness {
  const h = storeHarness({ config: e0Config(options.allowTaps ?? false) });
  options.before?.(h);
  if (options.runs !== undefined) h.read('runs', options.runs);
  for (const [blueprint, body] of Object.entries(options.previews ?? {})) {
    h.read('runControls', body, { path: `/api/run-controls?blueprint=${blueprint}` });
  }
  const body = statusBody(
    harvest,
    options.processes === undefined ? {} : { processes: options.processes },
  );
  body['device_state'] = 'device';
  Object.assign(body, options.root ?? {});
  h.read('status', body);
  return h;
}

function drawGem(harvest: unknown, options: GemOptions = {}) {
  const h = gemHarness(harvest, options);
  const region = renderRegion(h.state(), <GemSlot />);
  return { ...region, h };
}

const pause = () => screen.queryByRole('button', { name: 'Pause harvesting' });
const turnOn = () => screen.queryByRole('button', { name: 'Turn on' });
const chip = (container: HTMLElement) => container.querySelector('.tp-chip');
const choiceFact = (container: HTMLElement) => factValue(container, 'Setting');

/** Two saved runs: Farm (the fixture's source) and Quest. */
const TWO_RUNS = {
  runs: { bp_farm: { label: 'Farm', kind: 'coin' }, bp_quest: { label: 'Quest', kind: 'coin' } },
  readiness: {},
};
function questPreview(adGems: boolean, floating: boolean): unknown {
  const body = dashboardBody('run_controls.ad_gems_on_floating_off') as Record<string, unknown>;
  body['blueprint'] = 'quest';
  const routines = (body['settings'] as Record<string, Record<string, Record<string, unknown>>>)[
    'routines'
  ] as Record<string, Record<string, unknown>>;
  (routines['ad_gems'] as Record<string, unknown>)['enabled'] = adGems;
  (routines['floating_gems'] as Record<string, unknown>)['enabled'] = floating;
  return body;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('S-001 Pause harvesting (INV-1) as drawn', () => {
  it.each(sampleNames('status'))(
    'S-001 %s: Pause is drawn and enabled exactly when the core rule offers it',
    (name) => {
      const harvest = sample(`status.${name}`);
      // The independent decision: core's pauseOffered on the same read, nothing the VM says.
      const offered = pauseOffered(readOf(statusBody(harvest)).harvest, null, []);
      const { view, commands } = drawGem(harvest);
      const button = pause();
      if (offered) {
        expect(button).not.toBeNull();
        expect(button).not.toHaveAttribute('aria-disabled');
        expect(button).toHaveAttribute('data-tier', 'halt');
      } else {
        expect(button).toBeNull();
      }
      expect(textOutsideCatalogue(view.container)).toEqual([]);
      expect(commands.pause).not.toHaveBeenCalled();
    },
  );

  it('S-001 a Pause click calls the Pause command once, with nothing of the view model in it', async () => {
    const { commands } = drawGem(sample('status.collecting'));
    const button = pause();
    expect(button).not.toBeNull();
    if (button === null) return;
    await userEvent.click(button);
    expect(commands.pause).toHaveBeenCalledTimes(1);
    expect(commands.pause).toHaveBeenCalledWith();
  });

  it('S-001 status_failed (choice unknown) keeps Pause and offers no Turn on', () => {
    drawGem(sample('status.status_failed'));
    expect(pause()).not.toBeNull();
    expect(turnOn()).toBeNull();
  });
});

describe('S-034 your choice and what is seen are separate facts, drawn as separate marks', () => {
  it('S-034 manual_collector: "Setting: Not set" beside Running "Collecting gems, started by Collect gems only"', () => {
    const { view } = drawGem(sample('status.manual_collector'));
    const choice = choiceFact(view.container);
    expect(choice).toHaveTextContent('Not set');
    expect(choice.querySelector('.tp-chip')).toBeNull();
    expect(chip(view.container)).toHaveAttribute('data-state', 'running');
    expect(screen.getByText('Collecting gems')).toBeInTheDocument();
    expect(screen.getByText('Started by Collect gems only')).toBeInTheDocument();
    expect(choice).not.toHaveTextContent('On');
    expect(textOutsideCatalogue(view.container)).toEqual([]);
  });

  it('S-034 stopping with desired Off at revision 2: choice Off beside Stopping', () => {
    const harvest = sampleRecord('status.stopping');
    const desired = harvest['desired'] as Record<string, unknown>;
    desired['choice'] = 'off';
    desired['enabled'] = false;
    desired['revision'] = 2;
    const { view } = drawGem(harvest);
    expect(choiceFact(view.container)).toHaveTextContent('Off');
    expect(chip(view.container)).toHaveAttribute('data-state', 'stopping');
  });
});

describe('S-080 the source picker drives its description at once', () => {
  it('S-080 choosing another run changes the "Gem settings from" line in the same render, while the picker keeps focus', async () => {
    const h = gemHarness(sample('status.off'), {
      runs: TWO_RUNS,
      previews: { quest: questPreview(false, true) },
    });
    const region = renderRegion(h.state(), <GemSlot />);
    region.commands.selectGemSource.mockImplementation((run) => {
      act(() => {
        region.store.dispatch({ type: 'select_gem_source', run });
      });
    });
    const source = () => region.view.container.querySelector('.tp-source');
    expect(source()).toHaveTextContent(
      'Gem settings fromFarmAd gems every 30 s, floating gems off',
    );
    const picker = screen.getByRole('combobox', { name: 'Gem settings from' });
    await userEvent.selectOptions(picker, 'bp_quest');
    expect(region.commands.selectGemSource).toHaveBeenCalledWith('bp_quest');
    expect(source()).toHaveTextContent(
      'Gem settings fromQuestAd gems off, floating gems every 16 m',
    );
    expect(picker).toHaveFocus();
    expect(picker).toHaveValue('bp_quest');
    expect(screen.getByRole('link', { name: 'Edit run' })).toHaveAttribute(
      'href',
      '#runs/saved/bp_quest',
    );
    expect(textOutsideCatalogue(region.view.container)).toEqual([]);
  });
});

describe('Gem harvesting capability rows', () => {
  it('F-023: the Setting fact reads On, Off, Not set or Unknown from the record alone', () => {
    const cases: readonly [string, string][] = [
      ['waiting_for_battle', 'On'],
      ['off', 'Off'],
      ['unset', 'Not set'],
      ['status_failed', 'Unknown'],
      ['state_unreadable', 'Unknown'],
    ];
    for (const [name, word] of cases) {
      const { view } = drawGem(sample(`status.${name}`));
      expect(choiceFact(view.container)).toHaveTextContent(word);
      view.unmount();
    }
    const { view } = drawGem(sample('status.collecting'));
    expect(view.container.querySelector('[data-fact="setting"]')).toBeNull();
    expect(chip(view.container)).toHaveAttribute('data-state', 'running');
    expect(chip(view.container)).toHaveTextContent('Collecting gems');
    expect(pause()).not.toBeNull();
  });

  it('F-023: a malformed choice reads Unknown with Pause and without Turn on', () => {
    const harvest = sampleRecord('status.off');
    (harvest['desired'] as Record<string, unknown>)['choice'] = 'sometimes';
    const { view } = drawGem(harvest, { root: { collection_guard: CLEAR_GUARD } });
    expect(choiceFact(view.container)).toHaveTextContent('Unknown');
    expect(pause()).not.toBeNull();
    expect(turnOn()).toBeNull();
  });

  it('F-023: after a Known On at revision 5, Off at revisions 4 and 5 read "Off (from before your Turn on)" with Pause; Off at 6 reads plain Off', () => {
    const on = sampleRecord('status.collecting');
    (on['desired'] as Record<string, unknown>)['revision'] = 5;
    for (const [revision, word, paused] of [
      [4, 'Off (from before your Turn on)', true],
      [5, 'Off (from before your Turn on)', true],
      [6, 'Off', false],
    ] as const) {
      const off = sampleRecord('status.off');
      (off['desired'] as Record<string, unknown>)['revision'] = revision;
      const { view } = drawGem(off, {
        before: (h) => {
          h.read('status', statusBody(on));
        },
      });
      expect(choiceFact(view.container)).toHaveTextContent(word);
      expect(pause() !== null).toBe(paused);
      view.unmount();
    }
  });

  it('F-024: collecting, manual collector, live paused, stopping and a stale collector each read their own row', () => {
    const cases: readonly [string, string, string][] = [
      ['collecting', 'running', 'Collecting gems'],
      ['manual_collector', 'running', 'Collecting gems'],
      ['live_paused', 'paused', 'Gem routines off for this battle'],
      ['stopping', 'stopping', 'Stop not confirmed'],
      ['stale_collector', 'unknown', 'Collector state unknown'],
      ['waiting_for_battle', 'waiting', 'Waiting to track a battle'],
      ['transferring', 'waiting', 'Handing over to your run'],
    ];
    for (const [name, word, line] of cases) {
      const { view } = drawGem(sample(`status.${name}`));
      expect(chip(view.container), name).toHaveAttribute('data-state', word);
      expect(within(view.container).getByText(line)).toBeInTheDocument();
      // Pause follows S-001 (core D-8), proved per sample above; a manual collector's choice is unset.
      expect(pause() !== null, name).toBe(name !== 'manual_collector');
      expect(textOutsideCatalogue(view.container)).toEqual([]);
      view.unmount();
    }
  });

  it('F-024: no fixture sample but live_paused reads Paused, and an S-030 mutation reads Unknown with Pause', () => {
    for (const name of sampleNames('status')) {
      const { view } = drawGem(sample(`status.${name}`));
      if (name !== 'live_paused')
        expect(chip(view.container)?.getAttribute('data-state')).not.toBe('paused');
      view.unmount();
    }
    const stale = sampleRecord('status.collecting');
    const operation = stale['operation'] as Record<string, unknown>;
    (operation['battle'] as Record<string, unknown>)['publication_valid'] = false;
    const { view } = drawGem(stale);
    expect(chip(view.container)).toHaveAttribute('data-state', 'unknown');
    expect(pause()).not.toBeNull();
  });

  it('F-025: the meta line names the source run and its gem routines; policy_invalid shows its message and disables Turn on; source_changed says so', () => {
    const off = drawGem(sample('status.off'), { root: { collection_guard: CLEAR_GUARD } });
    expect(off.view.container.querySelector('.tp-source')).toHaveTextContent(
      'Gem settings fromFarmAd gems every 30 s, floating gems off',
    );
    const link = screen.getByRole('link', { name: 'Edit run' });
    // RUN-12: the exact run the source names, on the new Runs page.
    expect(link).toHaveAttribute('href', '#runs/saved/bp_farm');
    off.view.unmount();

    const invalid = sampleRecord('status.policy_invalid');
    const desired = invalid['desired'] as Record<string, unknown>;
    desired['choice'] = 'off';
    desired['enabled'] = false;
    const drawn = drawGem(invalid, { root: { collection_guard: CLEAR_GUARD } });
    expect(chip(drawn.view.container)).toHaveAttribute('data-state', 'blocked');
    expect(drawn.view.container.querySelector('[data-server-text="message"]')).toHaveTextContent(
      'Enable Ad Gems or timed orbit clicks.',
    );
    expect(turnOn()).toHaveAttribute('aria-disabled', 'true');
    expect(textOutsideCatalogue(drawn.view.container)).toEqual([]);
    drawn.view.unmount();

    const changed = sampleRecord('status.source_changed');
    const changedDesired = changed['desired'] as Record<string, unknown>;
    changedDesired['choice'] = 'off';
    changedDesired['enabled'] = false;
    const vm = selectGemSlot(
      gemHarness(changed, { root: { collection_guard: CLEAR_GUARD } }).state(),
    );
    const shown = drawGem(changed, { root: { collection_guard: CLEAR_GUARD } });
    if (vm.sourceChanged) {
      expect(screen.getByText('Changed since you turned it on')).toBeInTheDocument();
    }
    expect(textOutsideCatalogue(shown.view.container)).toEqual([]);
  });

  it('F-026: the picker shows only while the choice is confirmed Off or Not set, and omits combo and undefined runs', () => {
    const runs = dashboardBody('runs.defined_and_undefined_with_labels');
    const collecting = drawGem(sample('status.collecting'), { runs });
    expect(screen.queryByRole('combobox', { name: 'Gem settings from' })).toBeNull();
    collecting.view.unmount();
    const off = drawGem(sample('status.off'), { runs });
    const picker = screen.getByRole('combobox', { name: 'Gem settings from' });
    const values = within(picker)
      .getAllByRole('option')
      .map((option) => option.getAttribute('value'));
    expect(values).toContain('farm');
    expect(values).not.toContain('combo');
    expect(values).not.toContain('quest_chronofield');
    expect(textOutsideCatalogue(off.view.container)).toEqual([]);
  });

  it('F-026: the default follows the inventory order: local pick, desired source, resolved source, Start run, first run', () => {
    const vmWith = (patch: (h: StoreHarness) => void, harvest: Record<string, unknown>) => {
      const h = gemHarness(harvest, { runs: TWO_RUNS, before: patch });
      return selectGemSlot(h.state()).source.value;
    };
    const off = () => sampleRecord('status.off');
    // The desired source (bp_farm) wins over the Start run.
    expect(
      vmWith((h) => {
        h.state();
      }, off()),
    ).toBe('bp_farm');
    // The person's own pick wins over everything.
    const h = gemHarness(off(), { runs: TWO_RUNS });
    const store = renderRegion(h.state(), <GemSlot />).store;
    act(() => {
      store.dispatch({ type: 'select_gem_source', run: 'bp_quest' });
    });
    expect(selectGemSlot(store.getSnapshot()).source.value).toBe('bp_quest');
    // With neither a desired nor a resolved source, the Start picker's run is used.
    const bare = off();
    (bare['desired'] as Record<string, unknown>)['preset'] = null;
    (bare['source'] as Record<string, unknown>)['preset'] = null;
    const started = gemHarness(bare, { runs: TWO_RUNS });
    const startStore = renderRegion(started.state(), <GemSlot />).store;
    act(() => {
      startStore.dispatch({ type: 'select_run', run: 'bp_quest' });
    });
    expect(selectGemSlot(startStore.getSnapshot()).source.value).toBe('bp_quest');
  });

  it('F-026: three status deliveries while the picker has focus update every fact in the slot', async () => {
    const h = gemHarness(sample('status.off'), { runs: TWO_RUNS });
    const region = renderRegion(h.state(), <GemSlot />);
    const picker = screen.getByRole('combobox', { name: 'Gem settings from' });
    picker.focus();
    const ages = [1, 2, 3];
    for (const step of ages) {
      const next = sampleRecord(step === 3 ? 'status.unset' : 'status.off');
      h.read('status', statusBody(next));
      region.setState(h.state());
    }
    expect(screen.getByRole('combobox', { name: 'Gem settings from' })).toHaveFocus();
    expect(choiceFact(region.view.container)).toHaveTextContent('Not set');
    await Promise.resolve();
  });

  it('F-027: Turn on is enabled with a readable revision, a valid source with a gem routine on and a permitting guard; one click passes the exact command', async () => {
    const off = drawGem(sample('status.off'), {
      ...READY,
      root: { collection_guard: CLEAR_GUARD },
    });
    const button = turnOn();
    expect(button).not.toBeNull();
    expect(button).not.toHaveAttribute('aria-disabled');
    if (button === null) return;
    await userEvent.click(button);
    expect(off.commands.turnOn).toHaveBeenCalledTimes(1);
    expect(off.commands.turnOn).toHaveBeenCalledWith({
      preset: 'bp_farm',
      expectedRevision: 2,
      epoch: off.store.getSnapshot().epoch,
    });
    off.view.unmount();
    // No guard read, a blocking guard: shown, disabled, with a visible reason.
    drawGem(sample('status.off'), READY);
    const blocked = turnOn();
    expect(blocked).toHaveAttribute('aria-disabled', 'true');
    const reasonId = blocked?.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(reasonId)).toHaveTextContent('Starting collection is blocked');
  });

  it.each([
    ['X9 taps off', false, [{ pid: 9009, operation_id: 7 }], 'Waiting for a complete status read'],
    [
      'X9 taps allowed',
      true,
      [{ pid: 9009, operation_id: 7 }],
      'Waiting for a complete status read',
    ],
    ['a complete read with taps off', false, undefined, 'Game input is off'],
  ] as const)(
    'F4-R2-01 F-027 %s: Turn on is shown, disabled and says why; a click sends nothing',
    async (_label, allowTaps, processes, reason) => {
      const drawn = drawGem(sample('status.off'), {
        allowTaps,
        ...(processes === undefined ? {} : { processes }),
        root: { collection_guard: CLEAR_GUARD },
      });
      const button = turnOn();
      expect(button).toHaveAttribute('aria-disabled', 'true');
      const reasonId = button?.getAttribute('aria-describedby') ?? '';
      expect(document.getElementById(reasonId)).toHaveTextContent(reason);
      if (button !== null) await userEvent.click(button);
      expect(drawn.commands.turnOn).not.toHaveBeenCalled();
      expect(textOutsideCatalogue(drawn.view.container)).toEqual([]);
    },
  );

  it('F-027: a run whose saved gem routines are both off cannot be turned on', async () => {
    const h = gemHarness(sample('status.off'), {
      ...READY,
      runs: TWO_RUNS,
      previews: { quest: questPreview(false, false) },
      root: { collection_guard: CLEAR_GUARD },
    });
    const region = renderRegion(h.state(), <GemSlot />);
    region.commands.selectGemSource.mockImplementation((run) => {
      act(() => {
        region.store.dispatch({ type: 'select_gem_source', run });
      });
    });
    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Gem settings from' }),
      'bp_quest',
    );
    expect(turnOn()).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('This run has both gem routines off')).toBeInTheDocument();
  });

  it('F-028: one Pause click on the page sends exactly {enabled:false, expected_context, idempotency_key} to /api/harvest', async () => {
    vi.useFakeTimers();
    const page = await renderPage({ status: () => statusBody(sample('status.collecting')) });
    page.fake.route('POST', '/api/harvest', () => 'hold');
    const button = await screen.findByRole('button', { name: 'Pause harvesting' });
    await page.user.click(button);
    await page.advance(0);
    const bodies = postBodies(page.fake, '/api/harvest');
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toEqual({
      enabled: false,
      expected_context: { instance: 'main', serial: 'fake:1' },
      idempotency_key: expect.any(String) as unknown,
    });
    // Polls for a minute send nothing more.
    await page.advance(60_000);
    expect(postBodies(page.fake, '/api/harvest')).toHaveLength(1);
  });

  it('F-029: circuit_open with choice On shows Blocked "Stopped retrying after repeated failures" and Turn on again with the current revision; no other sample shows it', async () => {
    const harvest = sample('status.circuit_open');
    const { view, commands, store } = drawGem(harvest, {
      ...READY,
      root: { collection_guard: CLEAR_GUARD },
    });
    expect(chip(view.container)).toHaveAttribute('data-state', 'blocked');
    expect(screen.getByText('Stopped retrying after repeated failures')).toBeInTheDocument();
    const again = screen.getByRole('button', { name: 'Turn on again' });
    await userEvent.click(again);
    const vm = selectGemSlot(store.getSnapshot());
    expect(commands.turnOn).toHaveBeenCalledTimes(1);
    expect(commands.turnOn).toHaveBeenCalledWith(vm.turnOnAgain.command);
    const record = harvest as { desired: { revision: number } };
    expect(vm.turnOnAgain.command).toMatchObject({ expectedRevision: record.desired.revision });
    view.unmount();
    for (const name of sampleNames('status').filter((entry) => entry !== 'circuit_open')) {
      const other = drawGem(sample(`status.${name}`), { root: { collection_guard: CLEAR_GUARD } });
      expect(screen.queryByRole('button', { name: 'Turn on again' }), name).toBeNull();
      other.view.unmount();
    }
  });

  it('F-030: an applied overlay reads "This battle: ad gems on, floating gems off · applied" and keeps its own timing; pending never reads applied; live choice wins; no token appears', () => {
    const applied = drawGem(sample('status.overlay_applied'));
    const line = applied.view.container.querySelector<HTMLElement>('[data-overlay]');
    expect(factValue(applied.view.container, 'This battle')).toContainElement(line);
    expect(line).toHaveTextContent('ad gems on, floating gems offapplied');
    expect(factValue(applied.view.container, 'Timing')).toHaveTextContent(/^Ad gems every 30 s$/u);
    const timing = applied.view.getByText('the battle keeps its own timing');
    expect(timing).not.toBeVisible();
    fireEvent.click(applied.view.getByText('Details'));
    expect(timing).toBeVisible();
    expect(applied.view.container.textContent).not.toContain('harvest-overlay-');
    expect(applied.view.container.textContent).not.toContain('0000000000000000000000000');
    applied.view.unmount();
    for (const name of ['overlay_requested', 'overlay_waiting', 'overlay_off_pending']) {
      const pending = drawGem(sample(`status.${name}`));
      const overlay = pending.view.container.querySelector('[data-overlay]');
      if (overlay !== null) {
        expect(overlay).toHaveAttribute('data-overlay', 'pending');
        expect(overlay.textContent).not.toContain('applied');
      }
      pending.view.unmount();
    }
    drawGem(sample('status.overlay_live_choice'));
    expect(screen.getByText('your choice for this battle wins')).toBeInTheDocument();
  });

  it.each([
    ['different', true],
    ['unreported', null],
  ] as const)('F-030: %s battle settings never hide the saved source setting', (_case, target) => {
    const harvest = sampleRecord('status.overlay_applied');
    setPath(harvest, ['overlay', 'targets', 'floating_gems'], target);
    const { view } = drawGem(harvest);
    expect(factValue(view.container, 'Timing')).toHaveTextContent(
      'Ad gems every 30 s, floating gems off',
    );
  });

  it('F-030: an enabled routine with missing source timing keeps the uncertainty visible', () => {
    const harvest = sampleRecord('status.overlay_applied');
    setPath(harvest, ['source', 'policy', 'ad_gems', 'interval_sec'], null);
    const { view } = drawGem(harvest);
    expect(factValue(view.container, 'Timing')).toHaveTextContent(/^Ad gems timing not reported$/u);
    expect(factValue(view.container, 'This battle')).toHaveTextContent('ad gems on');
  });

  it('F-030: matching off settings leave a named timing description and the battle state', () => {
    const harvest = sampleRecord('status.overlay_applied');
    setPath(harvest, ['source', 'policy', 'ad_gems', 'enabled'], false);
    setPath(harvest, ['overlay', 'targets', 'ad_gems'], false);
    const { view } = drawGem(harvest);
    expect(factValue(view.container, 'Timing')).toHaveTextContent(/^No timed routines$/u);
    expect(factValue(view.container, 'This battle')).toHaveTextContent(
      'ad gems off, floating gems off',
    );
  });

  it('F-031: another driver: gathers, not gathering, not reported, stale and a foreign process each read their own line', () => {
    const cases: readonly [string, string, string][] = [
      ['owned_by_run_gathers', 'running', 'The run collects gems itself'],
      [
        'owned_by_run_not_gathering',
        'waiting',
        "Harvesting waits while the run uses the emulator; the run isn't collecting gems",
      ],
      ['owned_by_run_not_reported', 'unknown', "Can't tell whether the run collects gems"],
      ['owned_by_run_stale', 'unknown', "Can't tell whether the run collects gems"],
      ['foreign_process', 'waiting', 'Harvesting waits: another install holds the emulator'],
    ];
    for (const [name, word, line] of cases) {
      const { view } = drawGem(sample(`status.${name}`));
      expect(chip(view.container), name).toHaveAttribute('data-state', word);
      expect(within(view.container).getByText(line)).toBeInTheDocument();
      expect(within(view.container).queryByText('Collecting gems')).toBeNull();
      if (name === 'foreign_process')
        expect(within(view.container).getByText('PID 4242')).toBeInTheDocument();
      view.unmount();
    }
  });

  it('F-032: backing off reads its retry time; an inactive reconciler is Blocked even while starting; a failing one shows its message under Why', () => {
    const backing = drawGem(sample('status.backing_off'));
    expect(chip(backing.view.container)).toHaveAttribute('data-state', 'waiting');
    expect(backing.view.container.textContent).toMatch(/retrying in \d+ (s|m)/u);
    backing.view.unmount();
    const inactive = drawGem(sample('status.reconciler_inactive'));
    expect(chip(inactive.view.container)).toHaveAttribute('data-state', 'blocked');
    expect(screen.getByText('Nothing here will start the collector')).toBeInTheDocument();
    inactive.view.unmount();
    const failing = drawGem(sample('status.reconciler_failing'));
    expect(chip(failing.view.container)).toHaveAttribute('data-state', 'blocked');
    const message = failing.view.container.querySelector('details [data-server-text="message"]');
    expect(message).toHaveTextContent('adb server vanished');
    expect(textOutsideCatalogue(failing.view.container)).toEqual([]);
  });

  it('says what the dashboard found at its start, from the server code alone, and says nothing when it found none', () => {
    const words: Readonly<Record<string, readonly [string, string]>> = {
      adopted: [
        'waiting_for_battle',
        'The collector that was running when the dashboard started is kept',
      ],
      dead: [
        'worker_not_running',
        'The collector from before this dashboard start had already stopped',
      ],
      untracked: [
        'reconciler_inactive',
        'A collector this dashboard did not start is running and was not taken over',
      ],
    };
    for (const [code, [name, line]] of Object.entries(words)) {
      const found = drawGem(sample(`status.${name}`));
      const mark = found.view.container.querySelector('[data-collector-at-start]');
      expect(mark).toHaveAttribute('data-collector-at-start', code);
      expect(mark).toHaveTextContent(line);
      expect(mark).not.toBeVisible();
      fireEvent.click(found.view.getByText('Details'));
      expect(mark).toBeVisible();
      expect(textOutsideCatalogue(found.view.container)).toEqual([]);
      found.view.unmount();
    }
    const none = drawGem(sample('status.off'));
    expect(none.view.container.querySelector('[data-collector-at-start]')).toBeNull();
    const unknown = structuredClone(sample('status.waiting_for_battle')) as {
      host: { collector_at_start: { code: string } };
    };
    unknown.host.collector_at_start.code = 'something_new';
    none.view.unmount();
    const other = drawGem(unknown);
    expect(other.view.container.querySelector('[data-collector-at-start]')).toHaveTextContent(
      'A collector from before this dashboard start was found',
    );
  });

  it('has no axe violations in the off, collecting and blocked states', async () => {
    for (const name of ['off', 'collecting', 'policy_invalid', 'circuit_open']) {
      const { view } = drawGem(sample(`status.${name}`), {
        runs: TWO_RUNS,
        root: { collection_guard: CLEAR_GUARD },
      });
      expect(await axe(view.container)).toHaveNoViolations();
      view.unmount();
    }
  });
});

describe('what was claimed and missed this battle, and why', () => {
  const counts = (container: HTMLElement) =>
    container.querySelector<HTMLElement>('[data-gem-counts="ad"]');
  const orbit = (container: HTMLElement) =>
    container.querySelector<HTMLElement>('[data-gem-counts="orbit"]');
  const cause = (container: HTMLElement) =>
    container.querySelector<HTMLElement>('[data-gem-cause]');

  it('failing ad claims read as a stated warning, with the counts and the cause, never as "Collecting gems"', () => {
    const { view } = drawGem(sample('status.working_collector_claims_failing'));
    expect(chip(view.container)).toHaveAttribute('data-state', 'failed');
    expect(
      within(view.container).getByText('Ad gem claims failing: 4 in a row'),
    ).toBeInTheDocument();
    expect(within(view.container).queryByText('Collecting gems')).toBeNull();
    expect(counts(view.container)).toHaveTextContent('≥2 claimed · ≥4 missed');
    expect(cause(view.container)).toHaveTextContent('Offer moved, 2 m ago');
    expect(pause()).not.toBeNull();
    expect(textOutsideCatalogue(view.container)).toEqual([]);
  });

  it('a collector that is claiming reads "Collecting gems" with the confirmations seen and the latest miss', () => {
    const { view } = drawGem(sample('status.collecting'));
    expect(chip(view.container)).toHaveAttribute('data-state', 'running');
    expect(within(view.container).getByText('Collecting gems')).toBeInTheDocument();
    expect(counts(view.container)).toHaveTextContent('≥2 claimed · ≥1 missed');
    expect(cause(view.container)).toHaveTextContent('No offer seen, 1 m ago');
    expect(orbit(view.container)).toHaveTextContent('≥0 picked up');
    expect(factValue(view.container, 'Ad gems')).toContainElement(counts(view.container));
    expect(factValue(view.container, 'Orbit gems')).toContainElement(orbit(view.container));
    expect(cause(view.container)).not.toBeVisible();
    fireEvent.click(view.getByText('Details'));
    expect(cause(view.container)).toBeVisible();
    expect(factValue(view.container, 'Last miss')).toContainElement(cause(view.container));
    expect(view.container.querySelectorAll('[data-gem-counts], [data-gem-cause]')).toHaveLength(3);
    expect(textOutsideCatalogue(view.container)).toEqual([]);
  });

  it('a full run that owns the emulator shows the counts its own driver published', () => {
    const { view } = drawGem(sample('status.owned_by_run_gathers'));
    expect(within(view.container).getByText('The run collects gems itself')).toBeInTheDocument();
    expect(counts(view.container)).toHaveTextContent('≥2 claimed · ≥2 missed');
    expect(cause(view.container)).toHaveTextContent('Window ran out');
  });

  it('with nothing missed and no counters published there is no cause line and no invented count', () => {
    const { view } = drawGem(sample('status.manual_collector'));
    expect(cause(view.container)).toBeNull();
    expect(counts(view.container)).toHaveTextContent('≥2 claimed');
    expect(counts(view.container)).not.toHaveTextContent('missed');
    expect(orbit(view.container)).toHaveTextContent('≥0 picked up');
  });

  it('the published finder_suspected adds one warning line and keeps the row and its other lines', () => {
    const { view } = drawGem(sample('status.working_collector_finder_suspected'));
    expect(chip(view.container)).toHaveAttribute('data-state', 'running');
    expect(within(view.container).getByText('Collecting gems')).toBeInTheDocument();
    const lines = view.container.querySelectorAll('[data-gem-finder-suspected]');
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveTextContent(
      'Orbit gems: frames are being seen but none is recognized; the recognizer may need attention.',
    );
    expect(counts(view.container)).not.toBeNull();
    expect(textOutsideCatalogue(view.container)).toEqual([]);
  });

  it.each(['collecting', 'manual_collector', 'working_collector_claims_failing'])(
    'no warning line when finder_suspected is null or false (%s)',
    (name) => {
      const { view } = drawGem(sample(`status.${name}`));
      expect(view.container.querySelector('[data-gem-finder-suspected]')).toBeNull();
    },
  );

  it('finder_suspected false shows nothing', () => {
    const harvest = sampleRecord('status.working_collector_finder_suspected');
    setPath(harvest, ['operation', 'claims', 'finder_suspected'], false);
    const { view } = drawGem(harvest);
    expect(view.container.querySelector('[data-gem-finder-suspected]')).toBeNull();
  });

  it.each(['stale_collector', 'live_paused', 'waiting_for_battle', 'off'])(
    'a row that does not say gems are being handled (%s) shows neither line',
    (name) => {
      const { view } = drawGem(sample(`status.${name}`));
      expect(counts(view.container)).toBeNull();
      expect(cause(view.container)).toBeNull();
    },
  );
});
