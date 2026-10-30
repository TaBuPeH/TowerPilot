import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it, vi } from 'vitest';
import { copy } from '../../../src/copy/catalogue.ts';
import { helpTipName } from '../../../src/copy/helpTip.ts';
import { ActionRow, Reason } from '../../../src/ui/ActionRow.tsx';
import { Button } from '../../../src/ui/Button.tsx';
import { CopyLine } from '../../../src/ui/CopyLine.tsx';
import { Fact, Facts } from '../../../src/ui/Facts.tsx';
import { Region, RegionBody } from '../../../src/ui/Region.tsx';
import { SHOWN_LIMIT, ShownList } from '../../../src/ui/ShownList.tsx';
import { Tile, Tiles } from '../../../src/ui/Tile.tsx';
import { textOutsideCatalogue } from '../copy/renderedText.ts';

// Catalogue text only: an entry with a "why", one without, and short labels.
const WITH_WHY = copy.routes.notFound;
const SHORT = copy.routes.dashboard;
const OTHER = copy.routes.runs;
const NAME = copy.app.name;

describe('Tile (surface grammar)', () => {
  it('is a region named by its heading, with intro, header end and body in that order', async () => {
    const { container } = render(
      <Region id="demo" title={NAME.short} fallback={SHORT.short}>
        <RegionBody>
          <Tile
            titleId="tile-a"
            title={SHORT.short}
            intro={WITH_WHY}
            end={<a href="#runs">{OTHER.short}</a>}
            className="tp-feature-hook"
          >
            <p>{NAME.short}</p>
          </Tile>
        </RegionBody>
      </Region>,
    );
    const tile = screen.getByRole('region', { name: SHORT.short });
    expect(tile).toHaveClass('tp-tile', 'tp-feature-hook');
    const heading = within(tile).getByRole('heading', { level: 3, name: SHORT.short });
    expect(heading).toHaveAttribute('id', 'tile-a');
    expect(heading).toHaveAttribute('tabindex', '-1');
    const link = within(tile).getByRole('link', { name: OTHER.short });
    expect(heading.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(tile).getByText(WITH_WHY.short)).toBeVisible();
    expect(within(tile).getByRole('button', { name: helpTipName(WITH_WHY.short) })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(tile).not.toHaveAttribute('data-density');
    expect(tile).not.toHaveAttribute('data-tone');
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('nests as h2 > h3 > h4 without axe violations, and marks density and tone', async () => {
    const { container } = render(
      <Region id="demo" title={NAME.short} fallback={SHORT.short}>
        <RegionBody>
          <Tiles min="narrow">
            <Tile titleId="outer" title={SHORT.short} tone="attention">
              <Tile titleId="inner" title={OTHER.short} level={4} density="compact">
                <p>{NAME.short}</p>
              </Tile>
            </Tile>
          </Tiles>
        </RegionBody>
      </Region>,
    );
    expect(screen.getByRole('heading', { level: 4, name: OTHER.short })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: SHORT.short })).toHaveAttribute(
      'data-tone',
      'attention',
    );
    expect(screen.getByRole('region', { name: OTHER.short })).toHaveAttribute(
      'data-density',
      'compact',
    );
    expect(container.querySelector('.tp-tiles')).toHaveAttribute('data-min', 'narrow');
    expect(await axe(container)).toHaveNoViolations();
  });

  it('as a fieldset is a group named by its first-child legend, with no heading', async () => {
    const { container } = render(
      <Tile as="fieldset" titleId="group-a" title={SHORT.short} end={<span>{OTHER.short}</span>}>
        <label>
          <span>{NAME.short}</span>
          <input type="text" />
        </label>
      </Tile>,
    );
    const group = screen.getByRole('group', { name: SHORT.short });
    expect(group.tagName).toBe('FIELDSET');
    expect(group.firstElementChild?.tagName).toBe('LEGEND');
    expect(group.firstElementChild).toHaveAttribute('id', 'group-a');
    expect(screen.queryByRole('heading')).toBeNull();
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('Facts', () => {
  it.each(['rows', 'cells'] as const)(
    'draws %s as dl > div > dt + dd, never one fused line',
    async (layout) => {
      const { container } = render(
        <Facts layout={layout} className="tp-feature-facts">
          <Fact label={SHORT.short} value={OTHER.short} id="fact-a" />
          <Fact label={NAME.short} value={3} />
        </Facts>,
      );
      const list = container.querySelector('dl');
      expect(list).toHaveClass('tp-facts', 'tp-feature-facts');
      expect(list).toHaveAttribute('data-layout', layout);
      expect(screen.getAllByRole('term').map((term) => term.textContent)).toEqual([
        SHORT.short,
        NAME.short,
      ]);
      expect(screen.getAllByRole('definition').map((value) => value.textContent)).toEqual([
        OTHER.short,
        '3',
      ]);
      expect(container.querySelector('#fact-a')?.tagName).toBe('DD');
      for (const item of list?.children ?? []) {
        expect([...item.children].map((child) => child.tagName)).toEqual(['DT', 'DD']);
      }
      expect(textOutsideCatalogue(container)).toEqual([]);
      expect(await axe(container)).toHaveNoViolations();
    },
  );
});

describe('ActionRow and Reason', () => {
  it("keeps a disabled button's reason visible and its description exactly the short line", async () => {
    const onClick = vi.fn();
    const { container } = render(
      <ActionRow
        reason={<Reason id="row-reason" entry={WITH_WHY} />}
        outcome={<p>{OTHER.short}</p>}
      >
        <Button
          tier="secondary"
          label={SHORT.short}
          disabled
          reasonId="row-reason"
          onClick={onClick}
        />
        <Button tier="quiet" label={NAME.short} onClick={onClick} />
      </ActionRow>,
    );
    const blocked = screen.getByRole('button', { name: SHORT.short });
    expect(blocked).toHaveAccessibleDescription(WITH_WHY.short);
    expect(screen.getByText(WITH_WHY.short)).toBeVisible();
    // The "why" is one click away, beside the reason, never inside the description.
    const why = screen.getByRole('button', { name: helpTipName(WITH_WHY.short) });
    expect(why.closest('.tp-actions__reason')).not.toBeNull();
    expect(why).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(why);
    expect(screen.getByRole('tooltip')).toHaveTextContent(WITH_WHY.why ?? '');
    expect(blocked).toHaveAccessibleDescription(WITH_WHY.short);
    await userEvent.keyboard('{Escape}');
    await userEvent.click(blocked);
    expect(onClick).not.toHaveBeenCalled();
    // Buttons first, then the reason, then the outcome.
    const parts = [...(container.querySelector('.tp-actions')?.children ?? [])].map(
      (part) => part.className,
    );
    expect(parts).toEqual(['tp-actions__buttons', 'tp-actions__reason', 'tp-actions__outcome']);
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('draws only the buttons when nothing is blocked and nothing has happened', () => {
    const { container } = render(
      <ActionRow reason={null}>
        <Button tier="quiet" label={NAME.short} onClick={vi.fn()} />
      </ActionRow>,
    );
    expect(container.querySelector('.tp-actions')?.children).toHaveLength(1);
  });

  it('renders a reason without a why as the short line alone', () => {
    const { container } = render(<Reason id="plain" entry={SHORT} />);
    expect(container.querySelector('details')).toBeNull();
    expect(container.querySelector('#plain')).toHaveTextContent(SHORT.short);
  });
});

describe('CopyLine variants', () => {
  it('marks intro and note, and leaves the body line as it was', async () => {
    const { container } = render(
      <>
        <CopyLine entry={WITH_WHY} />
        <CopyLine entry={WITH_WHY} variant="intro" />
        <CopyLine entry={SHORT} variant="note" />
      </>,
    );
    const lines = [...container.querySelectorAll('.tp-copy-line')];
    expect(lines.map((line) => line.getAttribute('data-variant'))).toEqual([null, 'intro', 'note']);
    expect(lines[1]?.querySelector('button')).toHaveAccessibleName(helpTipName(WITH_WHY.short));
    expect(lines[2]?.querySelector('details')).toBeNull();
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('ShownList', () => {
  const items = Array.from({ length: 25 }, (_, index) => index + 1);
  const draw = (limit?: number) =>
    render(
      <ShownList
        items={items}
        itemKey={(item) => String(item)}
        render={(item) => <span>{item}</span>}
        more={copy.common.showMore.short}
        focusKey="demo-more"
        {...(limit === undefined ? {} : { limit })}
      />,
    );

  it('shows 10 items by default and folds the rest behind one counted disclosure', async () => {
    const { container } = draw();
    expect(SHOWN_LIMIT).toBe(10);
    const [shown, rest] = [...container.querySelectorAll('ul.tp-shown')];
    expect(shown?.children).toHaveLength(10);
    expect(rest?.children).toHaveLength(15);
    const summary = container.querySelector('summary');
    expect(summary).toHaveTextContent(copy.common.showMore.short);
    expect(summary?.querySelector('.tp-disclosure__count')).toHaveTextContent('15');
    expect(summary).toHaveAttribute('data-focus-key', 'demo-more');
    expect(textOutsideCatalogue(container)).toEqual([]);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('closed-details probe: folded items are in the document and found by text, but not visible until opened', async () => {
    const { container } = draw();
    const folded = screen.getByText('11');
    expect(folded).toBeInTheDocument();
    expect(folded).not.toBeVisible();
    expect(screen.getByText('10')).toBeVisible();
    // Record how role queries treat closed <details> content in this environment:
    // page tests that count rows must know whether folded rows are included.
    const listItems = screen.getAllByRole('listitem').length;
    expect(listItems).toBe(25);
    const summary = container.querySelector('summary');
    if (summary === null) throw new Error('missing disclosure');
    await userEvent.click(summary);
    expect(folded).toBeVisible();
  });

  it('draws no disclosure when everything fits, and only the disclosure when the limit is 0', () => {
    const fits = render(
      <ShownList
        items={[1, 2, 3]}
        itemKey={(item) => String(item)}
        render={(item) => <span>{item}</span>}
        more={copy.common.showMore.short}
      />,
    );
    expect(fits.container.querySelector('details')).toBeNull();
    expect(fits.container.querySelectorAll('li')).toHaveLength(3);
    fits.unmount();
    const { container } = draw(0);
    expect(container.querySelectorAll('ul.tp-shown')).toHaveLength(1);
    expect(container.querySelector('details ul.tp-shown')?.children).toHaveLength(25);
  });
});

describe('Region titleId', () => {
  it('keeps `${id}-title` by default and takes a page-chosen heading id when given', async () => {
    const { container } = render(
      <>
        <Region id="plain" title={SHORT.short} fallback={NAME.short}>
          <RegionBody>
            <p>{NAME.short}</p>
          </RegionBody>
        </Region>
        <Region id="chosen" titleId="page-editor" title={OTHER.short} fallback={NAME.short}>
          <RegionBody>
            <p>{NAME.short}</p>
          </RegionBody>
        </Region>
      </>,
    );
    expect(screen.getByRole('heading', { level: 2, name: SHORT.short })).toHaveAttribute(
      'id',
      'plain-title',
    );
    const chosen = screen.getByRole('region', { name: OTHER.short });
    expect(chosen).toHaveAttribute('aria-labelledby', 'page-editor');
    expect(chosen).toHaveAttribute('data-region', 'chosen');
    expect(within(chosen).getByRole('heading', { level: 2 })).toHaveAttribute('id', 'page-editor');
    expect(await axe(container)).toHaveNoViolations();
  });
});
