import { expect, it } from 'vitest';
import { own } from '../../../../../src/core/read.ts';
import { StartNext } from '../../../../../src/features/dashboard/startNext/StartNext.tsx';
import { selectStartNext } from '../../../../../src/store/selectors/startNext.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { addBody, addCase, regionState } from '../../../store/regionHelpers.ts';
import { dashboardBody } from '../../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../../support/fixture.ts';
import { renderRegion } from '../harness.tsx';

/**
 * Root review of the sketch screenshot: "Ready" (the run's recognition
 * readiness) is never drawn beside a Blocked Start, and a blocked Start or
 * Collect is disabled with its own visible reason.
 */
function withGuards(name: string) {
  const guards = dashboardBody(`status.${name}`);
  return addBody(addCase(regionState(), 'runs', 'runs.ready'), 'status', {
    ...statusBody(sample('status.off')),
    setup_battle_guard: own(guards, 'setup_battle_guard'),
    collection_guard: own(guards, 'collection_guard'),
  });
}

it('F-038: with both guards blocking the same operation, Start and Collect are disabled, share one reason naming both, and no "Ready" is drawn', () => {
  const state = withGuards('guards_setup_and_collection_blocking_the_same_operation');
  expect(selectStartNext(state).readiness.line).toBe('ready');
  const { view } = renderRegion(state, <StartNext />);
  for (const name of ['Start playing', 'Collect gems only']) {
    const button = view.getByRole('button', { name });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    const reason = document.getElementById(button.getAttribute('aria-describedby') ?? '');
    expect(reason).toHaveTextContent(/^Start playing and Collect gems only:/u);
    expect(reason).toHaveTextContent('Blocked');
  }
  expect(view.container.querySelectorAll('.tp-start-next__reason')).toHaveLength(1);
  expect(view.queryByText('Ready')).toBeNull();
  expect(textOutsideCatalogue(view.container)).toEqual([]);
});

it('F-038: with the guards clear and the run ready, Start is enabled and "Ready" is drawn', () => {
  const { view } = renderRegion(withGuards('guards_clear'), <StartNext />);
  expect(view.getByRole('button', { name: 'Start playing' })).not.toHaveAttribute('aria-disabled');
  expect(view.getByText('Ready')).toBeInTheDocument();
});
