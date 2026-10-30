import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { enterText, renderPage } from './harness.tsx';

// Capture the real lifecycle callback so expiry can be exercised without making
// a deliberately timed-out test contaminate the rest of the suite.
const lifecycle = vi.hoisted(() => ({
  finish: [] as (() => void | Promise<void>)[],
  afterEach: [] as (() => void | Promise<void>)[],
}));
vi.mock('vitest', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vitest')>();
  return {
    ...actual,
    afterEach: (finish: () => void | Promise<void>, timeout?: number) => {
      lifecycle.afterEach.push(finish);
      actual.afterEach(finish, timeout);
    },
    onTestFinished: (finish: () => void | Promise<void>, timeout?: number) => {
      lifecycle.finish.push(finish);
      actual.onTestFinished(finish, timeout);
    },
  };
});

beforeEach(() => {
  lifecycle.finish.length = 0;
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

function finishPage() {
  const finish = lifecycle.finish.at(-1);
  if (finish === undefined) throw new Error('page did not register teardown');
  return finish();
}

it('waits for an in-flight advance before finishing the page', async () => {
  const page = await renderPage();
  const order: string[] = [];
  const advancing = page.advance(1_000).then(() => order.push('advanced'));
  const finishing = Promise.resolve(finishPage()).then(() => order.push('finished'));
  await Promise.all([advancing, finishing]);
  expect(order).toEqual(['advanced', 'finished']);
});

it('waits for an in-flight user event before finishing the page', async () => {
  const page = await renderPage();
  const input = document.createElement('input');
  page.view.container.append(input);
  const order: string[] = [];
  const typing = page.user.type(input, 'in flight').then(() => order.push('typed'));
  const finishing = Promise.resolve(finishPage()).then(() => order.push('finished'));
  await Promise.all([typing, finishing]);
  expect(input).toHaveValue('in flight');
  expect(order).toEqual(['typed', 'finished']);
});

it('rejects advances and user events after teardown without dispatching input', async () => {
  const page = await renderPage();
  const input = document.createElement('input');
  page.view.container.append(input);
  await finishPage();
  await expect(page.advance()).rejects.toThrow('test that has ended');
  expect(() => page.user.type(input, 'late')).toThrow('test that has ended');
  expect(input).toHaveValue('');
});

it('drains every page before cleanup and stops each only once across both hooks', async () => {
  expect(lifecycle.afterEach).toHaveLength(1);
  const first = await renderPage();
  const second = await renderPage();
  const firstStop = vi.spyOn(first.data, 'stop');
  const secondStop = vi.spyOn(second.data, 'stop');
  const input = document.createElement('input');
  second.view.container.append(input);
  const typing = second.user.type(input, 'before cleanup');
  // The harness registers first; this file's useRealTimers hook registers later.
  const drain = lifecycle.afterEach[0];
  if (drain === undefined) throw new Error('page did not register afterEach');
  await drain();
  expect(input).toHaveValue('before cleanup');
  await typing;
  await Promise.all(lifecycle.finish.map(async (finish) => finish()));
  expect(firstStop).toHaveBeenCalledTimes(1);
  expect(secondStop).toHaveBeenCalledTimes(1);
});

it('keeps an in-flight rejection visible to its caller while finishing the page', async () => {
  const page = await renderPage();
  const stop = vi.spyOn(page.data, 'stop');
  const failure = new Error('in-flight advance failed');
  vi.spyOn(vi, 'advanceTimersByTimeAsync').mockRejectedValueOnce(failure);
  const advancing = expect(page.advance()).rejects.toBe(failure);
  await Promise.all([advancing, finishPage()]);
  expect(stop).toHaveBeenCalledTimes(1);
  await expect(page.advance()).rejects.toThrow('test that has ended');
});

it('refuses a disabled target without pasting into the unrelated focused input', async () => {
  const page = await renderPage();
  const target = document.createElement('input');
  target.disabled = true;
  const unrelated = document.createElement('input');
  unrelated.value = 'keep this';
  page.view.container.append(target, unrelated);
  unrelated.focus();
  await expect(enterText(page.user, target, 'must not leak')).rejects.toThrow(
    'enterText: element did not take focus',
  );
  expect(target).toHaveValue('');
  expect(unrelated).toHaveValue('keep this');
});

it('refuses to paste when a target click leaves focus on an unrelated control', async () => {
  const page = await renderPage();
  const target = document.createElement('input');
  const unrelated = document.createElement('input');
  unrelated.value = 'keep this';
  page.view.container.append(target, unrelated);
  target.addEventListener('mousedown', (event) => {
    event.preventDefault();
  });
  unrelated.focus();
  await expect(enterText(page.user, target, 'must not leak')).rejects.toThrow(
    'enterText: element did not take focus',
  );
  expect(target).toHaveValue('');
  expect(unrelated).toHaveValue('keep this');
  expect(unrelated).toHaveFocus();
});

it('pastes exact text into the target after it takes focus', async () => {
  const page = await renderPage();
  const target = document.createElement('textarea');
  const unrelated = document.createElement('input');
  unrelated.value = 'keep this';
  page.view.container.append(target, unrelated);
  unrelated.focus();
  const text = ' exact text\nwith spacing & punctuation! ';
  await enterText(page.user, target, text);
  expect(target).toHaveFocus();
  expect(target).toHaveValue(text);
  expect(unrelated).toHaveValue('keep this');
});
