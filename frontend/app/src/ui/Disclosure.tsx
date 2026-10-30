import type { CopyNode, CopyText } from '../copy/text.ts';
import './ui.css';

export interface DisclosureProps {
  readonly label: CopyText;
  readonly count?: number;
  /** One line shown beside the label while closed. */
  readonly preview?: CopyNode;
  /** A stable key that survives polls (F-113). */
  readonly focusKey?: string;
  readonly children: CopyNode;
}

/**
 * Detail on demand (spec 3.5): native details and summary, so it works without
 * script and keeps its open state across renders while its key is stable.
 */
export function Disclosure({ label, count, preview, focusKey, children }: DisclosureProps) {
  return (
    <details className="tp-disclosure">
      <summary data-focus-key={focusKey}>
        <span>{label}</span>
        {count === undefined ? null : <span className="tp-disclosure__count">{count}</span>}
        {preview === undefined ? null : <span className="tp-disclosure__preview">{preview}</span>}
      </summary>
      <div className="tp-disclosure__body">{children}</div>
    </details>
  );
}
