import { act, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { fromServer } from '../../../src/copy/server.ts';
import { createStore, useStoreSelector, viewEqual } from '../../../src/store/store.ts';

describe('createStore', () => {
  it('notifies subscribers of a new snapshot and skips an unchanged one', () => {
    const store = createStore({ count: 0 });
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.update((state) => ({ count: state.count + 1 }));
    store.update((state) => state);
    expect(store.getSnapshot()).toEqual({ count: 1 });
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    store.update(() => ({ count: 5 }));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

interface State {
  readonly count: number;
  readonly other: number;
}

const selectView = (state: State) => ({ label: `count ${String(state.count)}` });

describe('viewEqual', () => {
  it('compares plain view models by value and server text by its text', () => {
    expect(viewEqual({ a: [1, { b: 'x' }] }, { a: [1, { b: 'x' }] })).toBe(true);
    expect(viewEqual({ a: [1] }, { a: [2] })).toBe(false);
    expect(viewEqual(fromServer('name', 'Alpha'), fromServer('name', 'Alpha'))).toBe(true);
    expect(viewEqual(fromServer('name', 'Alpha'), fromServer('name', 'Beta'))).toBe(false);
    expect(viewEqual(new Map(), new Map())).toBe(false);
  });

  it('skips a re-render when the new view model equals the previous one', () => {
    const store = createStore<State>({ count: 1, other: 0 });
    let renders = 0;
    const select = (state: State) => ({ label: `count ${String(state.count)}` });
    function Probe() {
      renders += 1;
      const view = useStoreSelector(store, select, viewEqual);
      return <output>{view.label}</output>;
    }
    render(<Probe />);
    const before = renders;
    act(() => {
      store.update((state) => ({ ...state, other: state.other + 1 }));
    });
    expect(renders).toBe(before);
    act(() => {
      store.update((state) => ({ ...state, count: 3 }));
    });
    expect(renders).toBe(before + 1);
    expect(screen.getByRole('status')).toHaveTextContent('count 3');
  });
});

describe('useStoreSelector', () => {
  it('re-renders with the selected view model when the snapshot changes', () => {
    const store = createStore<State>({ count: 1, other: 0 });
    let renders = 0;
    function Probe() {
      renders += 1;
      const view = useStoreSelector(store, selectView);
      return <output>{view.label}</output>;
    }
    render(<Probe />);
    expect(screen.getByRole('status')).toHaveTextContent('count 1');
    const before = renders;
    act(() => {
      store.update((state) => ({ ...state, count: 2 }));
    });
    expect(screen.getByRole('status')).toHaveTextContent('count 2');
    expect(renders).toBe(before + 1);
  });
});
