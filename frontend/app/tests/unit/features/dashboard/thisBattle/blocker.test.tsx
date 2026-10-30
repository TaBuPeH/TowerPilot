import { within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ownPath } from '../../../../../src/core/read.ts';
import { ThisBattle } from '../../../../../src/features/dashboard/thisBattle/ThisBattle.tsx';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { addBody, regionState } from '../../../store/regionHelpers.ts';
import { dashboardBody } from '../../../../support/dashboardFixture.ts';
import { renderRegion } from '../harness.tsx';

/**
 * F-055 "the blocker word otherwise": a routine's blocker is a runtime code;
 * the Next column reads a word for it, never the code itself in plain view.
 */
function withBlocker(code: string) {
  const body = dashboardBody('battles_active.runtime_ad_orbit_shopping');
  const adGems = ownPath(body, 'battle', 'runtime', 'routines', 'ad_gems');
  if (typeof adGems !== 'object' || adGems === null) throw new Error('no ad_gems routine');
  Reflect.set(adGems, 'blocker', code);
  return addBody(regionState(), 'battlesActive', body);
}

function nextCell(view: ReturnType<typeof renderRegion>['view']) {
  const name = view.getByText('Ad gems', { selector: 'strong' });
  const row = name.closest('.tp-battle__routine');
  if (row === null) throw new Error('no Ad gems row');
  const cell = row.querySelector('.tp-battle__next');
  if (!(cell instanceof HTMLElement)) throw new Error('no Next cell');
  return cell;
}

it('F-055: a recorded blocker code reads as its word (not_observed: "Not seen on screen yet"), never the code in plain view', () => {
  const { view } = renderRegion(withBlocker('not_observed'), <ThisBattle />);
  const cell = nextCell(view);
  expect(within(cell).getByText('Not seen on screen yet')).toBeInTheDocument();
  expect(cell.textContent).not.toContain('not_observed');
  expect(textOutsideCatalogue(view.container)).toEqual([]);
});

it('F-055: an unknown blocker code reads "blocked", with the code as sent only under Why', () => {
  const { view } = renderRegion(withBlocker('brand_new_reason'), <ThisBattle />);
  const cell = nextCell(view);
  expect(within(cell).getByText('Blocked')).toBeInTheDocument();
  const server = cell.querySelector('details [data-server-text="message"]');
  expect(server).toHaveTextContent('brand_new_reason');
  expect(textOutsideCatalogue(view.container)).toEqual([]);
});
