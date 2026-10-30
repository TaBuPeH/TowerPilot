import type { CopyEntry, CopyNode } from '../copy/text.ts';
import { Why } from './Why.tsx';
import './ui.css';

export interface ReasonProps {
  /** The id a disabled Button names as its `reasonId`. */
  readonly id: string;
  readonly entry: CopyEntry;
}

/**
 * A disabled control's visible reason (spec 3.3, 8.6). The id sits on the short
 * line alone, so the button's description is exactly that line; the "why", when
 * there is one, opens from the help icon beside it.
 */
export function Reason({ id, entry }: ReasonProps) {
  return (
    <span className="tp-reason">
      <span id={id}>{entry.short}</span>
      <Why subject={entry.short} why={entry.why} />
    </span>
  );
}

export interface ActionRowProps {
  /** The row's buttons, in tab order. */
  readonly children: CopyNode;
  /** Visible reasons for disabled buttons, with optional background help icons after the actions. */
  readonly reason?: CopyNode;
  /** The newest outcome of the row's commands. */
  readonly outcome?: CopyNode;
}

/** A row of commands with their visible reasons and their outcome below. */
export function ActionRow({ children, reason, outcome }: ActionRowProps) {
  return (
    <div className="tp-actions">
      <div className="tp-actions__buttons">{children}</div>
      {reason === undefined || reason === null ? null : (
        <div className="tp-actions__reason">{reason}</div>
      )}
      {outcome === undefined || outcome === null ? null : (
        <div className="tp-actions__outcome">{outcome}</div>
      )}
    </div>
  );
}
