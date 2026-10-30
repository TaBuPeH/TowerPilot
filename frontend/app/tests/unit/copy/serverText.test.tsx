import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import {
  fromServer,
  observeServerText,
  serverTextContent,
  type ServerText,
  type ServerTextKind,
} from '../../../src/copy/server.ts';
import { TopBarAccount } from '../../../src/features/dashboard/TopBarAccount.tsx';
import { selectTopBar } from '../../../src/store/selectors/topBar.ts';
import { ServerValue } from '../../../src/ui/ServerValue.tsx';
import { textOutsideCatalogue } from './renderedText.ts';

/** Server text the way a store selector makes it; throws when there is none. */
function made<K extends ServerTextKind>(kind: K, raw: string): ServerText<K> {
  const value = fromServer(kind, raw);
  if (value === null) {
    throw new Error('fromServer returned null');
  }
  return value;
}

function shown(container: Element): string | null | undefined {
  return container.querySelector('[data-server-text]')?.textContent;
}

describe('ServerText and ServerValue (dashboard-spec 6.3, R5-04, R5-05)', () => {
  it.each(['Tower Pilot', 'Why', 'Dashboard'])(
    'renders server text equal to catalogue text (%j) inside the marker',
    (raw) => {
      const { container } = render(
        <ServerValue kind="name" value={made('name', raw)} label={copy.routes.dashboard.short} />,
      );
      expect(shown(container)).toBe(raw);
      expect(textOutsideCatalogue(container)).toEqual([]);
    },
  );

  it('reports marked text that fromServer never made', () => {
    const { container } = render(<bdi data-server-text="name">Never made by fromServer</bdi>);
    expect(textOutsideCatalogue(container)).toHaveLength(1);
  });

  it('reports a marker that holds elements, catalogue text included', () => {
    const { container } = render(
      <bdi data-server-text="name">
        <span>{copy.app.name.short}</span>
      </bdi>,
    );
    expect(textOutsideCatalogue(container)).toHaveLength(1);
  });

  it('reports an element in a marker even when it has no text', () => {
    const { container } = render(
      <bdi data-server-text="name">
        <span />
      </bdi>,
    );
    expect(textOutsideCatalogue(container)).toHaveLength(1);
  });

  it('reports server text rendered outside the marker', () => {
    const value = made('name', 'Shard farming on the server');
    const { container } = render(<span>{serverTextContent(value)}</span>);
    expect(textOutsideCatalogue(container)).toHaveLength(1);
  });

  it('documented limit: outside the marker, server text equal to catalogue text passes the value check', () => {
    const value = made('name', 'Tower Pilot');
    const { container } = render(<span>{serverTextContent(value)}</span>);
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it('keeps a multi-line message exactly as sent', () => {
    const raw =
      'Automation input could not be revoked.\nThe marker file was not written: C:\\TowerPilot\\input.lock\n\nTry again.';
    const { container } = render(<ServerValue kind="message" value={made('message', raw)} />);
    expect(shown(container)).toBe(raw);
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it('keeps a message longer than 500 characters with its actionable suffix', () => {
    const raw = `${'The lease check failed again. '.repeat(25)}Restart the emulator, then press Turn input off again.`;
    expect(raw.length).toBeGreaterThan(500);
    const { container } = render(<ServerValue kind="message" value={made('message', raw)} />);
    expect(shown(container)).toBe(raw);
  });

  it('shows control characters visibly instead of dropping them', () => {
    expect(serverTextContent(made('message', 'bell\u0007here'))).toBe('bell\\u{7}here');
    expect(serverTextContent(made('detail', 'tab\tand\nnewline'))).toBe('tab\tand\nnewline');
    for (const kind of ['name', 'message', 'detail', 'id'] as const) {
      expect(serverTextContent(made(kind, 'a\u0000\u001f\u007f\u0085\u009fb'))).toBe(
        'a\\u{0}\\u{1f}\\u{7f}\\u{85}\\u{9f}b',
      );
    }
  });

  it.each(['message', 'detail'] as const)('keeps whitespace and bidi controls in %s', (kind) => {
    const raw = '  first\tline\r\n\n\u202esecond\u202c  ';
    const { container } = render(<ServerValue kind={kind} value={made(kind, raw)} />);
    expect(shown(container)).toBe(raw);
    expect(container.querySelector('bdi')?.getAttribute('data-server-text')).toBe(kind);
  });

  it('keeps a long unbroken message whole', () => {
    const raw = `${'x'.repeat(600)}\nTry Turn input off again.`;
    const { container } = render(<ServerValue kind="message" value={made('message', raw)} />);
    expect(shown(container)).toBe(raw);
  });

  it('makes a name one line, and nothing at all from no text', () => {
    expect(serverTextContent(made('name', '  My\n  Tower  '))).toBe('My Tower');
    expect(fromServer('name', '   ')).toBeNull();
    expect(fromServer('message', 42)).toBeNull();
    expect(fromServer('message', null)).toBeNull();
    expect(serverTextContent(made('id', '  run\r\n\tid  '))).toBe('run id');
    expect(serverTextContent(made('name', 'x'.repeat(600)))).toHaveLength(600);
    for (const kind of ['name', 'message', 'detail', 'id'] as const) {
      expect(fromServer(kind, '\t\r\n ')).toBeNull();
    }
  });

  it('changing the server message changes only the server-text node', () => {
    const view = render(
      <ServerValue
        kind="message"
        value={made('message', 'First answer')}
        label={copy.common.why.short}
      />,
    );
    const before = view.container.innerHTML;
    view.rerender(
      <ServerValue
        kind="message"
        value={made('message', 'Second answer')}
        label={copy.common.why.short}
      />,
    );
    expect(view.container.innerHTML).toBe(before.replace('First answer', 'Second answer'));
  });

  it('is the shown string itself: nothing to unwrap, nothing hidden', () => {
    const value = made('name', 'Real issued name');
    expect(typeof value).toBe('string');
    expect(serverTextContent(value)).toBe('Real issued name');
  });

  it('observes shown text until unsubscribed, separately from catalogue text', () => {
    const heard: string[] = [];
    const stop = observeServerText((text) => heard.push(text));
    made('name', '  Visible\nname  ');
    fromServer('name', null);
    stop();
    made('name', 'Not observed');
    expect(heard).toEqual(['Visible name']);
  });
});

describe('the selector to TopBarAccount slice (F-002, R5-06)', () => {
  it('shows the matching account as bare server text under the section label', () => {
    const vm = selectTopBar({
      body: { active: 'a', accounts: [{ id: 'a', label: 'My Tower' }] },
      observedAt: 12,
    });
    const { container } = render(<TopBarAccount vm={vm} />);
    expect(shown(container)).toBe('My Tower');
    // The top bar's Account section names it; the slice carries the value alone.
    expect(container.textContent).toBe('My Tower');
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(vm.account.kind === 'known' ? vm.account.observedAt : null).toBe(12);
  });

  it('shows No active account when no account matches', () => {
    const vm = selectTopBar({
      body: { active: 'absent', accounts: [{ id: 'a', label: 'My Tower' }] },
      observedAt: 12,
    });
    const { container } = render(<TopBarAccount vm={vm} />);
    expect(container.textContent).toBe('No active account');
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it.each([
    null,
    { body: null, observedAt: 12 },
    { body: { active: 'a', accounts: [{ id: 'a', label: '  ' }] }, observedAt: 12 },
    { body: { active: 'a', accounts: [{ id: 'a', label: 'Farm' }] }, observedAt: NaN },
  ])('shows unknown for an unreadable or missing observation (%j)', (read) => {
    const { container } = render(<TopBarAccount vm={selectTopBar(read)} />);
    expect(container.textContent).toBe('Account unknown');
    expect(textOutsideCatalogue(container)).toEqual([]);
  });
});
