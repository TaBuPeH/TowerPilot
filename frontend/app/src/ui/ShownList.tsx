import type { CopyNode, CopyText } from '../copy/text.ts';
import { Disclosure } from './Disclosure.tsx';
import './ui.css';

/** How many items a long list shows before the rest fold away (council-tiles synthesis). */
export const SHOWN_LIMIT = 10;

export interface ShownListProps<T> {
  readonly items: readonly T[];
  /** A stable key per item (it survives polls). */
  readonly itemKey: (item: T) => string;
  readonly render: (item: T) => CopyNode;
  /** The disclosure's label; its count is how many items it holds. */
  readonly more: CopyText;
  /** How many items show before the disclosure (default 10). */
  readonly limit?: number;
  /** A stable key for the disclosure's summary (F-113). */
  readonly focusKey?: string;
  /** Feature hook classes, kept beside `tp-shown` on both lists. */
  readonly className?: string;
}

/**
 * A bounded list: the first `limit` items, then the rest behind one Disclosure
 * with their count. Every item stays in the document, so find-in-page and the
 * Disclosure's native open state keep working; nothing pages or scrolls away.
 */
export function ShownList<T>({
  items,
  itemKey,
  render,
  more,
  limit = SHOWN_LIMIT,
  focusKey,
  className,
}: ShownListProps<T>) {
  const list = className === undefined ? 'tp-shown' : `tp-shown ${className}`;
  const cut = Math.max(0, Math.floor(limit));
  const shown = items.slice(0, cut);
  const rest = items.slice(cut);
  const draw = (part: readonly T[]) => (
    <ul className={list}>
      {part.map((item) => (
        <li key={itemKey(item)}>{render(item)}</li>
      ))}
    </ul>
  );
  return (
    <div className="tp-shown-list">
      {shown.length === 0 ? null : draw(shown)}
      {rest.length === 0 ? null : (
        <Disclosure
          label={more}
          count={rest.length}
          {...(focusKey === undefined ? {} : { focusKey })}
        >
          {draw(rest)}
        </Disclosure>
      )}
    </div>
  );
}
