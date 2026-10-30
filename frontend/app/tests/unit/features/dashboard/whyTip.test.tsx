import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { copy } from '../../../../src/copy/catalogue.ts';
import { helpTipName } from '../../../../src/copy/helpTip.ts';
import { WhyTip } from '../../../../src/features/dashboard/shared/WhyTip.tsx';

const line = copy.dashboard.thisBattle.tryNotice;

describe('WhyTip: a catalogue background as a help icon, not a Why disclosure', () => {
  it('draws nothing for a line without background', () => {
    const { container } = render(<WhyTip subject={line.short} why={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is one icon named for its line; opened, the tip holds exactly the background', () => {
    const { container } = render(<WhyTip subject={line.short} why={line.why} />);
    expect(container.querySelector('details')).toBeNull();
    expect(screen.queryByText(line.why ?? '')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: helpTipName(line.short) }));
    expect(screen.getByRole('tooltip')).toHaveTextContent(line.why ?? '');
  });
});
