import type { CopyNode, CopyText } from '../copy/text.ts';
import { Chip, type ChipState } from './Chip.tsx';
import { Why } from './Why.tsx';
import './ui.css';

export interface NoticeProps {
  /** The status word; null for a settled result shown without a chip (spec 3.4). */
  readonly state: ChipState | null;
  readonly age?: CopyText;
  /** Where the line belongs ("Automation - Stop gem collector, 20 s ago:"). */
  readonly lead?: CopyNode;
  /** The one plain line. */
  readonly line: CopyNode;
  /** Server text, through ServerValue. */
  readonly server?: CopyNode;
  readonly why?: CopyText;
  /** At most one main and one secondary action, then Dismiss. */
  readonly actions?: CopyNode;
  readonly focusKey?: string;
}

/**
 * A notice or outcome line (spec 3.3, 3.4): chip, one line, help, actions. It
 * has no live role of its own; announcements go through the one live region.
 */
export function Notice({ state, age, lead, line, server, why, actions, focusKey }: NoticeProps) {
  return (
    <div className="tp-notice" data-state={state ?? 'settled'} data-focus-key={focusKey}>
      <div className="tp-notice__text">
        {lead === undefined ? null : <span className="tp-notice__lead">{lead}</span>}
        {state === null ? null : <Chip state={state} {...(age === undefined ? {} : { age })} />}
        <span className="tp-notice__line">{line}</span>
        <Why why={why} />
        {server === undefined ? null : <span className="tp-notice__server">{server}</span>}
      </div>
      {actions === undefined ? null : <div className="tp-notice__actions">{actions}</div>}
    </div>
  );
}
