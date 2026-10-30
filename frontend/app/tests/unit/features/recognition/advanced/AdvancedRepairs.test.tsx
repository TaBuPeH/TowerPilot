import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, expect, it } from 'vitest';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../../../scripts/contract-fixture.mjs';
import { createRecognitionAdvancedApi } from '../../../../../src/api/recognitionAdvanced.ts';
import { contextFromConfig } from '../../../../../src/api/context.ts';
import { ownPath } from '../../../../../src/core/read.ts';
import { scanPlan } from '../../../../../src/core/recognitionImages.ts';
import { AdvancedRepairs } from '../../../../../src/features/recognition/advanced/AdvancedRepairs.tsx';
import {
  createRecognitionAdvancedStore,
  currentAdvanced,
} from '../../../../../src/store/recognitionAdvanced.ts';
import { initialDashboardState } from '../../../../../src/store/dashboardStore.ts';
import type { DashboardCase } from '../../../../support/dashboardFixture.ts';
import { createFakeTransport, createMemoryStorage } from '../../../../support/fakeTransport.ts';
import { renderRegion } from '../../dashboard/harness.tsx';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { selectAdvanced } from '../../../../../src/store/selectors/recognitionAdvanced.ts';
const cases = loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture;
function fixture(family: string, name: string): DashboardCase {
  const c = ownPath(cases, 'cases', family, name);
  if (c === undefined) throw new Error(`${family}.${name}`);
  return structuredClone(c) as DashboardCase;
}
const cleanups: (() => void)[] = [];
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
});
async function change(action: () => void | Promise<void>) {
  await act(async () => {
    await action();
  });
}
async function setup(
  options: {
    save?: string;
    status?: string;
    mount?: boolean;
    storage?: ReturnType<typeof createMemoryStorage>;
  } = {},
) {
  const fake = createFakeTransport();
  const config = fixture('calibrate_writes', 'read_the_configuration_after_the_wall_bar_apply');
  const context = contextFromConfig(config.body);
  if (context === null) throw new Error('Fixture context missing');
  fake.route('GET', '/api/config', () => config);
  fake.route('GET', '/api/wizard/templates', () =>
    fixture('recognition_reads', 'templates_captured_listed_by_folder_and_name'),
  );
  fake.route('GET', '/api/wizard/required', () =>
    fixture('recognition_reads', 'required_images_some_captured'),
  );
  fake.route('GET', '/api/catalogue/modules', () =>
    fixture('recognition_reads', 'module_catalogue_one_module_learned_on_this_install'),
  );
  fake.route('GET', /\/api\/wizard\/scan-plan/, () =>
    fixture(
      'recognition_reads',
      'scan_plan_shows_a_learned_rectangle_and_makes_the_target_automatic',
    ),
  );
  let status = options.status ?? 'status_done_with_matches_and_an_ambiguous_one';
  fake.route('GET', /\/api\/screen-analysis\/status/, () => fixture('screen_analysis', status));
  fake.route('POST', '/api/screen-analysis/start', () =>
    fixture('screen_analysis', 'start_accepted'),
  );
  fake.route('POST', /\/api\/template\//, () =>
    fixture('recognition_images', options.save ?? 'save_a_crop_of_the_cached_frame'),
  );
  const storage = options.storage ?? createMemoryStorage();
  const api = createRecognitionAdvancedApi(fake.transport, storage, 30);
  const store = createRecognitionAdvancedStore(api);
  cleanups.push(store.dispose);
  await change(() => {
    store.setTarget(context, 1, true);
  });
  await waitFor(() => {
    expect(currentAdvanced(store.getSnapshot())?.busy).toBe(false);
  });
  const mounted =
    options.mount === false
      ? null
      : renderRegion(
          initialDashboardState(),
          <main>
            <AdvancedRepairs store={store} run={null} />
          </main>,
        );
  return {
    fake,
    store,
    api,
    context,
    storage,
    mounted,
    current: () => currentAdvanced(store.getSnapshot()),
    status: (next: string) => {
      status = next;
    },
  };
}
async function frame(s: Awaited<ReturnType<typeof setup>>) {
  await change(async () => {
    await s.store.takeFrame();
  });
  const token = s.current()?.frame?.token;
  if (token === undefined) throw new Error('No token');
  await change(() => {
    s.store.frameLoaded(token, { width: 1080, height: 2560 });
    s.store.edit('name', 'buttons/retry.png');
    s.store.draw({ x: 100, y: 200, w: 300, h: 90 });
  });
  return token;
}
it('R11 the crop target has one format hint and one validation message shared by Save', async () => {
  await setup();
  const name = screen.getByLabelText('Template target name');
  expect(name).toHaveAccessibleDescription(
    'Relative PNG path, for example buttons/retry.png. Enter a safe relative PNG target name.',
  );
  expect(screen.getAllByText('Enter a safe relative PNG target name.')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Save crop' })).toHaveAccessibleDescription(
    'Enter a safe relative PNG target name.',
  );
});

it('R9 one analysis click sends once with context and idempotency key; retry only reads', async () => {
  const s = await setup();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Analyze this screen' }));
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  const body = JSON.parse(s.fake.writes()[0]?.body ?? '{}') as Record<string, unknown>;
  expect(body['expected_context']).toEqual({ ...s.context, rendering: null });
  expect(body).toEqual({
    expected_context: { ...s.context, rendering: null },
    idempotency_key: s.current()?.launch?.key,
  });
  expect(typeof body['idempotency_key']).toBe('string');
  await user.click(screen.getByRole('button', { name: 'Read analysis status' }));
  expect(s.fake.writes()).toHaveLength(1);
  expect(s.current()?.selectedOperation).toBe('000000ee-0000-4000-8000-000000000000');
  expect(
    screen.queryByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toBeNull();
}, 15000);
it('R9 an operation switch clears the previous snapshot and matches; only succeeded selected operations show images', async () => {
  const s = await setup();
  const done = fixture('screen_analysis', 'status_done_with_matches_and_an_ambiguous_one');
  const id = String(ownPath(done.body, 'operation', 'id'));
  await change(async () => {
    s.store.selectOperation(id);
    await s.store.readAnalysis();
  });
  expect(
    screen.getByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toHaveAttribute('src', expect.stringContaining(id));
  expect(screen.getByText('Ambiguous')).toBeVisible();
  expect(screen.getByText('No manifest role reported')).toBeVisible();
  await change(() => {
    s.store.selectOperation('another-operation');
  });
  expect(screen.queryByRole('img')).toBeNull();
  await change(async () => {
    await s.store.readAnalysis();
  });
  expect(screen.queryByRole('img')).toBeNull();
  expect(screen.queryByText('SecondWind')).toBeNull();
  expect(screen.queryByText('1799999900', { exact: true })).toBeNull();
  expect(screen.queryByText('212', { exact: true })).toBeNull();
  expect(screen.queryByText('home', { exact: true })).toBeNull();
  expect(screen.queryByText('Observed screen')).toBeNull();
  expect(screen.queryByText('Candidate screens')).toBeNull();
});
it('R9 F6R10-R1-02 a warm failed analysis read withdraws the entire previous result bundle', async () => {
  const s = await setup();
  await change(async () => {
    await s.store.readAnalysis();
  });
  expect(screen.getByText('1799999900', { exact: true })).toBeVisible();
  expect(screen.getByText('212', { exact: true })).toBeVisible();
  expect(
    screen.getByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toBeVisible();
  s.fake.route('GET', /\/api\/screen-analysis\/status/, () => 'hold');
  await change(async () => {
    const reading = s.store.readAnalysis();
    s.fake.next(/\/api\/screen-analysis\/status/).lose();
    await reading;
  });
  expect(screen.getByText('Unavailable', { exact: true })).toBeVisible();
  expect(
    screen.getByText(
      'This read failed. Any retained data is an older observation; retry the read.',
    ),
  ).toBeVisible();
  expect(screen.queryByText('1799999900', { exact: true })).toBeNull();
  expect(screen.queryByText('212', { exact: true })).toBeNull();
  expect(screen.queryByText('home', { exact: true })).toBeNull();
  expect(screen.queryByText('Observed screen')).toBeNull();
  expect(screen.queryByText('Candidate screens')).toBeNull();
  expect(screen.queryByText('SecondWind', { exact: true })).toBeNull();
  expect(
    screen.queryByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toBeNull();
  expect(s.fake.writes()).toEqual([]);
});
it('R9 F6R10-R1-04 analysis sends the exact five-field launch context from the click-time rendering read', async () => {
  const s = await setup();
  const config = fixture('calibrate_writes', 'read_the_configuration_after_the_wall_bar_apply');
  const instance = ownPath(config.body, 'instances', s.context.instance) as Record<string, unknown>;
  // Analysis launch recordings use rendering:null. Change only the recorded config's rendering
  // to model a prepared emulator; the click must use this new descriptor, not the earlier read.
  const rendering = { width: 1080, height: 2560, density: 360 };
  instance['rendering'] = rendering;
  s.fake.route('GET', '/api/config', () => config);
  await change(async () => {
    await s.store.analyze();
  });
  expect(s.fake.writes()).toHaveLength(1);
  expect(s.fake.writes()[0]?.path).toBe('/api/screen-analysis/start');
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '{}')).toEqual({
    expected_context: { ...s.context, rendering },
    idempotency_key: s.current()?.launch?.key,
  });
});
it('R9 a lost launch stays Unknown on an unchanged status and is never resent', async () => {
  const s = await setup();
  s.fake.route('POST', '/api/screen-analysis/start', () => 'hold');
  await change(async () => {
    await s.store.analyze();
  });
  await change(() => {
    s.fake.next('/api/screen-analysis/start').lose();
  });
  await waitFor(() => {
    expect(s.current()?.launch?.phase).toBe('unconfirmed');
  });
  s.status('status_idle_nothing_was_ever_analysed');
  await change(async () => {
    await s.store.readAnalysis();
  });
  expect(s.current()?.launch?.readSettlement).toBeNull();
  await change(async () => {
    await s.store.analyze();
  });
  expect(s.fake.writes()).toHaveLength(1);
});
it('R10 plan keeps named missing targets and learned rectangles, with catalogue and library behind disclosures', async () => {
  const s = await setup();
  expect(
    screen.getByText('An empty missing-seen list does not mean coverage is complete.'),
  ).toBeVisible();
  expect(screen.getByText(/Capturing an image is not proof that recognition works/u)).toBeVisible();
  expect(screen.getByText('Grouped image library').closest('details')).not.toHaveAttribute('open');
  expect(
    scanPlan(s.current()?.plan)
      .steps.flatMap((step) => step.targets)
      .find((target) => target.rel === 'buttons/retry.png')?.status,
  ).toBe('Missing');
  const step = scanPlan(s.current()?.plan).steps.find((step) =>
    step.targets.some((target) => target.rel === 'buttons/retry.png'),
  );
  if (step?.title == null) throw new Error('No learned target step');
  const user = userEvent.setup();
  await user.click(screen.getByText('Capture plan', { exact: true }));
  await user.click(screen.getByText(step.title, { exact: true }));
  const targetRow = screen.getByText('buttons/retry.png', { exact: true }).closest('li');
  if (targetRow === null) throw new Error('No target row');
  await user.click(within(targetRow).getByRole('button', { name: 'Use as crop target' }));
  expect(screen.getByLabelText('y (native pixels)')).toHaveValue('2200');
});
it('R10 a context epoch change unmounts old library pixels and versions the next thumbnail', async () => {
  const s = await setup();
  const user = userEvent.setup();
  await user.click(screen.getByText('Grouped image library', { exact: true }));
  await user.click(screen.getByText('buttons', { exact: true }));
  const old = (await screen.findAllByRole('img', { name: 'Grouped image library' }))[0];
  if (old === undefined) throw new Error('No thumbnail');
  const src = old.getAttribute('src');
  await change(() => {
    s.store.setTarget(s.context, 2, true);
  });
  expect(old).not.toBeInTheDocument();
  await waitFor(() => {
    expect(s.current()?.busy).toBe(false);
  });
  await user.click(screen.getByText('buttons', { exact: true }));
  const next = (await screen.findAllByRole('img', { name: 'Grouped image library' }))[0];
  expect(next).not.toHaveAttribute('src', src);
  expect(s.fake.writes()).toEqual([]);
});
it('R10 a failed library thumbnail is explicit and a confirmed coverage read retries its pixels', async () => {
  const s = await setup();
  const user = userEvent.setup();
  await user.click(screen.getByText('Grouped image library', { exact: true }));
  await user.click(screen.getByText('buttons', { exact: true }));
  const old = (await screen.findAllByRole('img', { name: 'Grouped image library' }))[0];
  if (old === undefined) throw new Error('No thumbnail');
  const src = old.getAttribute('src');
  fireEvent.error(old);
  expect(
    screen.getByText('Image unavailable. Read coverage again to retry this thumbnail.'),
  ).toBeVisible();
  await change(async () => {
    await s.store.refresh();
  });
  expect(
    screen.queryByText('Image unavailable. Read coverage again to retry this thumbnail.'),
  ).toBeNull();
  const next = screen.getAllByRole('img', { name: 'Grouped image library' })[0];
  expect(next).not.toHaveAttribute('src', src);
  expect(old).not.toBeInTheDocument();
  expect(s.fake.writes()).toEqual([]);
});
it('R11 frame is captured only on click; status polling preserves its token and raw crop draft', async () => {
  const s = await setup();
  expect(s.current()?.frame).toBeNull();
  const token = await frame(s);
  await change(async () => {
    await s.store.refresh();
    await s.store.readAnalysis();
  });
  expect(s.current()?.frame?.token).toBe(token);
  expect(s.current()?.draft).toEqual({
    name: 'buttons/retry.png',
    x: '100',
    y: '200',
    w: '300',
    h: '90',
  });
  expect(
    screen.getByRole('img', { name: 'Captured still frame for a native-pixel crop' }),
  ).toHaveAttribute('src', `/api/frame.png?ts=${token}`);
});
it('R11 saves the exact frame and native crop once, without overwrite, and reports limited proof', async () => {
  const s = await setup();
  const token = await frame(s);
  await change(async () => {
    await s.store.save();
  });
  await waitFor(() => {
    expect(s.current()?.crop?.phase).toBe('confirmed');
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '{}')).toEqual({
    ts: token,
    x: 100,
    y: 200,
    w: 300,
    h: 90,
  });
  expect(
    screen.getByText(
      'File written. This proves neither provenance nor recognition on this emulator.',
    ),
  ).toBeVisible();
  expect(screen.getByText('0.371')).toBeVisible();
});
it('R11 an existing file requires separate named replacement consent; cancel sends nothing and restores focus', async () => {
  const s = await setup();
  await frame(s);
  await change(() => {
    s.store.edit('name', 'buttons/exit_battle.png');
  });
  const user = userEvent.setup();
  const replace = screen.getByRole('button', { name: 'Replace existing image' });
  await user.click(replace);
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText('Replace file: buttons/exit_battle.png')).toBeVisible();
  expect(s.fake.writes()).toHaveLength(0);
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
  expect(replace).toHaveFocus();
  expect(s.fake.writes()).toHaveLength(0);
  await user.click(replace);
  await user.click(screen.getByRole('button', { name: 'Replace this file' }));
  await waitFor(() => {
    expect(s.fake.writes()).toHaveLength(1);
  });
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '{}')).toMatchObject({ overwrite: true });
});
it.each([
  'save_from_a_frame_that_left_the_cache_409',
  'save_from_a_frame_fetched_for_another_account_409',
  'save_a_flat_colour_box_400',
  'save_over_an_existing_template_without_the_overwrite_flag_409',
])('R11 refusal preserves server words and crop draft: %s', async (save) => {
  const s = await setup({ save });
  await frame(s);
  await change(async () => {
    await s.store.save();
  });
  await waitFor(() => {
    expect(s.current()?.crop?.phase).toBe('refused');
  });
  expect(s.mounted?.view.container).toHaveTextContent(
    String(ownPath(fixture('recognition_images', save).body, 'error')),
  );
  expect(s.current()?.draft.x).toBe('100');
  expect(s.fake.writes()).toHaveLength(1);
});
it('R11 context changes drop the frame and keep name and raw rectangle drafts only under their original target', async () => {
  const s = await setup();
  await frame(s);
  await change(() => {
    s.store.edit('x', '2.5');
    s.store.setTarget({ ...s.context, account: 'other' }, 2, true);
  });
  expect(s.current()?.frame).toBeNull();
  expect(s.current()?.draft.name).toBe('');
  await change(() => {
    s.store.setTarget(s.context, 3, true);
  });
  expect(s.current()?.frame).toBeNull();
  expect(s.current()?.draft.x).toBe('2.5');
  expect(s.current()?.draft.name).toBe('buttons/retry.png');
});
it('R11 offline capture and malformed native coordinate drafts are disabled with visible reasons', async () => {
  const s = await setup();
  await frame(s);
  fireEvent.change(screen.getByLabelText('x (native pixels)'), { target: { value: '1.5' } });
  expect(screen.getByRole('button', { name: 'Save crop' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(screen.getByLabelText('x (native pixels)')).toHaveValue('1.5');
  await change(() => {
    s.store.setTarget(s.context, 1, false);
  });
  expect(screen.getByRole('button', { name: 'Take a frame' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(s.fake.writes()).toHaveLength(0);
});
it('R9 R10 R11 accessible regions and fields pass axe and keep focused draft through a coverage read', async () => {
  const s = await setup();
  expect(s.mounted?.view.container).not.toHaveTextContent(JSON.stringify(s.context));
  const input = screen.getByLabelText('Template target name');
  input.focus();
  await change(async () => {
    await s.store.refresh();
  });
  expect(input).toHaveFocus();
  expect(await axe(s.mounted?.view.container ?? document.body)).toHaveNoViolations();
  expect(textOutsideCatalogue(s.mounted?.view.container ?? document.body)).toEqual([]);
}, 15000);

it('R9 R10 R11 F6R10-R1-06 typed projections keep catalogue labels outside scalar server markers', async () => {
  const s = await setup();
  await change(async () => {
    await s.store.readAnalysis();
  });
  await frame(s);
  await change(async () => {
    await s.store.save();
  });
  await waitFor(() => {
    expect(s.current()?.crop?.phase).toBe('confirmed');
  });
  const user = userEvent.setup();
  for (const label of [
    'Mapping route coverage',
    'Interface coverage',
    'Capture plan',
    'Required images',
    'Images requested but not found',
    'Module naming catalogue',
    'Grouped image library',
  ]) {
    await user.click(screen.getByText(label, { exact: true }));
  }
  const firstStep = scanPlan(s.current()?.plan).steps[0]?.title;
  if (firstStep === undefined || firstStep === null) throw new Error('No plan title');
  await user.click(screen.getByText(firstStep, { exact: true }));
  await user.click(screen.getByText('Home', { exact: true }));
  await user.click(screen.getByText('buttons', { exact: true }));
  const container = s.mounted?.view.container;
  if (container === undefined) throw new Error('Not mounted');
  expect(textOutsideCatalogue(container)).toEqual([]);
  const serverText = [...container.querySelectorAll('[data-server-text]')].map(
    (el) => el.textContent,
  );
  expect(serverText).toContain('home');
  expect(serverText).toContain('buttons/retry.png');
  expect(serverText.every((value) => !value.startsWith('{') && !value.startsWith('['))).toBe(true);
  expect(
    Object.hasOwn(selectAdvanced(s.store.getSnapshot(), Date.now()).target ?? {}, 'launch'),
  ).toBe(false);
  expect(
    Object.hasOwn(selectAdvanced(s.store.getSnapshot(), Date.now()).target ?? {}, 'crop'),
  ).toBe(false);
}, 15000);

it('R9 only a later status naming the lost launch key settles it; a different key stays Unknown', async () => {
  const s = await setup({ mount: false });
  s.fake.route('POST', '/api/screen-analysis/start', () => 'hold');
  await s.store.analyze();
  s.fake.next('/api/screen-analysis/start').lose();
  await waitFor(() => {
    expect(s.current()?.launch?.phase).toBe('unconfirmed');
  });
  await s.store.readAnalysis();
  expect(s.current()?.launch?.readSettlement).toBeNull();
  const status = fixture('screen_analysis', 'status_done_with_matches_and_an_ambiguous_one');
  // No recorded answer can carry this test's freshly generated key. Change only its operation key to model that genuine later receipt.
  const operation = ownPath(status.body, 'operation') as Record<string, unknown>;
  operation['idempotency_key'] = s.current()?.launch?.key;
  s.fake.route('GET', /\/api\/screen-analysis\/status/, () => status);
  await s.store.readAnalysis();
  expect(s.current()?.launch?.readSettlement).toBe('applied');
  expect(s.current()?.selectedOperation).toBe(String(operation['id']));
  expect(s.fake.writes()).toHaveLength(1);
});
it('R9 a delayed answer from a previously selected operation cannot replace the new selection', async () => {
  const s = await setup({ mount: false });
  s.fake.route('GET', /\/api\/screen-analysis\/status/, () => 'hold');
  s.store.selectOperation('old');
  const reading = s.store.readAnalysis();
  s.store.selectOperation('new');
  const c = fixture('screen_analysis', 'status_done_with_matches_and_an_ambiguous_one');
  s.fake.next(/\/api\/screen-analysis\/status/).respond(c.status, c.body);
  await reading;
  expect(s.current()?.selectedOperation).toBe('new');
  expect(s.current()?.analysis).toBeNull();
});
it('R11 save freezes the clicked rectangle while a context check is held and retains later typing', async () => {
  const s = await setup({ mount: false });
  const token = await frame(s);
  s.fake.route('GET', '/api/config', () => 'hold');
  const save = s.store.save();
  s.store.edit('x', '321');
  const config = fixture('calibrate_writes', 'read_the_configuration_after_the_wall_bar_apply');
  s.fake.next('/api/config').respond(config.status, config.body);
  await save;
  expect(JSON.parse(s.fake.writes()[0]?.body ?? '{}')).toEqual({
    ts: token,
    x: 100,
    y: 200,
    w: 300,
    h: 90,
  });
  expect(s.current()?.draft.x).toBe('321');
});
it('R11 a context switch during the click check sends nothing and drops the frame', async () => {
  const s = await setup({ mount: false });
  await frame(s);
  s.fake.route('GET', '/api/config', () => 'hold');
  const save = s.store.save();
  s.store.setTarget({ ...s.context, account: 'other' }, 2, true);
  await save;
  expect(s.fake.writes()).toHaveLength(0);
  expect(s.current()?.frame).toBeNull();
});
it('R11 a lost crop stays Unknown after unchanged inventory reads and reload; it is never resent', async () => {
  const s = await setup({ mount: false });
  await frame(s);
  s.fake.route('POST', /\/api\/template\//, () => 'hold');
  await s.store.save();
  s.fake.next(/\/api\/template\//, 'POST').lose();
  await waitFor(() => {
    expect(s.current()?.crop?.phase).toBe('unconfirmed');
  });
  await s.store.refresh();
  expect(s.current()?.crop?.readSettlement).toBeNull();
  s.store.dispose();
  const restored = await setup({ mount: false, storage: s.storage });
  expect(restored.current()?.crop?.phase).toBe('unconfirmed');
  expect(restored.current()?.frame).toBeNull();
  expect(restored.current()?.draft.name).toBe('buttons/retry.png');
  await restored.store.save();
  expect(restored.fake.writes()).toEqual([]);
});

it.each([
  'status_running_with_the_progress_of_the_matching',
  'status_done_operation_whose_result_was_never_written_is_unavailable',
  'status_unknown_operation_409',
  'status_of_another_workflow_409',
])('R9 status retains running, unavailable and coded refusal distinctions: %s', async (status) => {
  const s = await setup({ status });
  const fixtureCase = fixture('screen_analysis', status);
  const named = ownPath(fixtureCase.body, 'operation', 'id');
  await change(async () => {
    s.store.selectOperation(typeof named === 'string' ? named : 'missing');
    await s.store.readAnalysis();
  });
  const code = ownPath(fixtureCase.body, 'code');
  if (typeof code === 'string') expect(screen.getByText(code, { exact: true })).toBeVisible();
  else
    expect(s.mounted?.view.container).toHaveTextContent(
      String(ownPath(fixtureCase.body, 'status')),
    );
  expect(
    screen.queryByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toBeNull();
  expect(s.fake.writes()).toEqual([]);
});
it('R9 stale analysis is marked as an older read while retaining its operation-bound observation', async () => {
  const s = await setup();
  await change(async () => {
    await s.store.readAnalysis();
  });
  await change(() => {
    s.store.update((state) => {
      const key = state.key;
      if (key === null) throw new Error('No context');
      const target = state.targets[key];
      if (target === undefined) throw new Error('No target');
      return {
        ...state,
        targets: { ...state.targets, [key]: { ...target, analysisAt: Date.now() - 31000 } },
      };
    });
  });
  expect(
    screen.getByText(
      'Stale read: more than 30 seconds old. Read again before relying on this observation.',
    ),
  ).toBeVisible();
  expect(
    screen.getByRole('img', { name: 'Still snapshot of this analysis operation' }),
  ).toBeVisible();
});
