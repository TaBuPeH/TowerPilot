import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LiveRegionProvider } from '../../../src/ui/LiveRegion.tsx';
import { RegionBoundary } from '../../../src/ui/RegionBoundary.tsx';
import { renderPage } from '../features/dashboard/harness.tsx';
import { copy, elapsedSeconds } from '../../../src/copy/catalogue.ts';
import type { CopyText } from '../../../src/copy/text.ts';
import { Button } from '../../../src/ui/Button.tsx';
import { Chip } from '../../../src/ui/Chip.tsx';
import { CopyLine } from '../../../src/ui/CopyLine.tsx';
import { Why } from '../../../src/ui/Why.tsx';
import { textOutsideCatalogue } from './renderedText.ts';

function goTo(hash: string) {
  act(() => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
}

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

function Boom(): never {
  throw new Error('render failed');
}

/** A realistic notes field (R3-R4-03): a controlled textarea named by catalogue copy. */
function Notes({ initial }: { readonly initial: string }) {
  const [text, setText] = useState(initial);
  return (
    <textarea
      aria-label={copy.app.name.short}
      value={text}
      onChange={(event) => {
        setText(event.currentTarget.value);
      }}
    />
  );
}

/**
 * A realistic Dashboard metric (R3-R3-06): a catalogue label and a value
 * formatted by a catalogue formatter, shown as text and announced through
 * aria-label. The same component is a clean control in the lint corpus
 * (metric_formatter_control).
 */
function Metric({ label, value }: { readonly label: CopyText; readonly value: CopyText }) {
  return (
    <div className="tp-metric">
      <span>{label}</span>
      <output aria-label={value}>{value}</output>
    </div>
  );
}

describe('rendered text comes from the copy catalogue (runtime backstop of ADR-001 rule 5)', () => {
  it('finds invented wording in text, visible attributes and button-input labels', () => {
    // tests are outside the src/ lint rules, so this can render what src/ cannot
    const { container } = render(
      <div>
        <p title="Invented title">Invented text</p>
        <input type="submit" value="Invented label" />
        <span aria-label="Invented name">{copy.app.name.short}</span>
        <span data-state="not visible">{42}</span>
      </div>,
    );
    expect(textOutsideCatalogue(container)).toEqual([
      'text in <p>: "Invented text"',
      '<p title>: "Invented title"',
      '<input value>: "Invented label"',
      '<span aria-label>: "Invented name"',
    ]);
  });

  it('accepts a catalogue formatter output as visible text and as an aria-label (R3-R3-06)', () => {
    const { container } = render(
      <Metric label={copy.routes.dashboard.short} value={elapsedSeconds(12)} />,
    );
    const output = container.querySelector('output');
    expect(output).toHaveTextContent('12 seconds');
    expect(output).toHaveAttribute('aria-label', '12 seconds');
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it('accepts numbers as React prints them, exponent form included, and reports NaN', () => {
    const { container } = render(
      <p>
        <output>{12}</output>
        <output>{-3.5}</output>
        <output>{1e21}</output>
        <output>{1e-7}</output>
        <output>{Number.NaN}</output>
      </p>,
    );
    expect(textOutsideCatalogue(container)).toEqual(['text in <output>: "NaN"']);
  });

  it('accepts what a person types into a controlled textarea, from empty (R3-R4-03)', async () => {
    const { container } = render(<Notes initial="" />);
    await userEvent.type(screen.getByRole('textbox'), 'Edited by the user');
    expect(screen.getByRole('textbox')).toHaveValue('Edited by the user');
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it('accepts a controlled textarea that starts with a value (R3-R4-03)', () => {
    const { container } = render(<Notes initial="Draft from before" />);
    expect(screen.getByRole('textbox')).toHaveValue('Draft from before');
    expect(textOutsideCatalogue(container)).toEqual([]);
  });

  it("still checks a form control's visible attributes and option labels", () => {
    // discriminating control: values are skipped, labels and hints are not
    const { container } = render(
      <form>
        <textarea
          aria-label={copy.app.name.short}
          placeholder="Invented hint"
          defaultValue="typed text"
        />
        <input aria-label={copy.app.name.short} defaultValue="typed input" />
        <select aria-label={copy.app.name.short} defaultValue="b">
          <option value="a">{copy.routes.dashboard.short}</option>
          <option value="b">Invented option</option>
        </select>
      </form>,
    );
    expect(textOutsideCatalogue(container)).toEqual([
      'text in <option>: "Invented option"',
      '<textarea placeholder>: "Invented hint"',
    ]);
  });

  it('still reports the same shape of wording when the catalogue did not make it', () => {
    // discriminating control: "12 minutes" is never produced by a catalogue formatter
    const { container } = render(
      <div className="tp-metric">
        <span>{copy.routes.dashboard.short}</span>
        <output aria-label="12 minutes">12 minutes</output>
      </div>,
    );
    expect(textOutsideCatalogue(container)).toEqual([
      'text in <output>: "12 minutes"',
      '<output aria-label>: "12 minutes"',
    ]);
  });

  it.each([
    ['the Dashboard', ''],
    ['the not-found page', '#nowhere'],
    ['the Dashboard after a route change', '#dashboard'],
  ])('the app shell on %s shows only catalogue text', async (_name, hash) => {
    vi.useFakeTimers();
    const { view } = await renderPage();
    const { container } = view;
    if (hash !== '') {
      goTo('#nowhere');
      goTo(hash);
    }
    expect(container.textContent).not.toBe('');
    expect(textOutsideCatalogue(document.body)).toEqual([]);
  });

  it('the design-system components and the region fallback show only catalogue text', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined); // React reports the caught render error
    const { container } = render(
      <LiveRegionProvider>
        <Chip state="unknown" />
        <Button tier="primary" label={copy.dashboard.openCurrent.short} onClick={() => undefined} />
        <CopyLine entry={copy.errors.regionFailed} />
        <Why why={copy.routes.notFound.why} />
        <RegionBoundary resetKey="a" fallback={<CopyLine entry={copy.errors.regionFailed} />}>
          <Boom />
        </RegionBoundary>
      </LiveRegionProvider>,
    );
    expect(container.textContent).toContain(copy.errors.regionFailed.short);
    expect(textOutsideCatalogue(container)).toEqual([]);
  });
});
