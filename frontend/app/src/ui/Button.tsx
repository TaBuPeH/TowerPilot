import { useId } from 'react';
import type { CopyText } from '../copy/text.ts';
import './ui.css';

/** primary: Start playing only; halt: every Stop, Stop again, Pause harvesting, Turn input off; quiet: links, Dismiss. */
export type ButtonTier = 'primary' | 'secondary' | 'halt' | 'quiet';

/** A disabled button always names the visible element that says why (spec 3.3, 8.6). */
type Availability =
  | { readonly disabled?: false; readonly reasonId?: undefined }
  | { readonly disabled: true; readonly reasonId: string };

export type ButtonProps = Availability & {
  readonly tier: ButtonTier;
  /** The visible label. */
  readonly label: CopyText;
  readonly onClick: () => void;
  /**
   * The id of the visible element that names the target ("gem collector,
   * started 11:43, PID 4101"): the accessible name becomes label plus target.
   */
  readonly targetId?: string;
  /** Extra description ids (reason lines, ages). */
  readonly describedBy?: string;
  /** A stable key that survives re-renders and replacements (spec 8.3, 8.11). */
  readonly focusKey?: string;
  /** A request of this control is in flight; the button stays enabled and focusable. */
  readonly busy?: boolean;
  readonly id?: string;
};

/**
 * A command button. Always `type="button"`. A disabled button uses
 * aria-disabled, so it stays focusable (a poll that disables the focused
 * control never drops focus to the page) and a click does nothing.
 */
export function Button(props: ButtonProps) {
  const { tier, label, onClick, targetId, describedBy, focusKey, busy = false, id } = props;
  const own = useId();
  const labelId = `${own}-label`;
  const disabled = props.disabled === true;
  const described = [disabled ? props.reasonId : undefined, describedBy]
    .filter((value) => value !== undefined)
    .join(' ');
  return (
    <button
      type="button"
      id={id}
      className="tp-button"
      data-tier={tier}
      data-focus-key={focusKey}
      aria-disabled={disabled ? true : undefined}
      aria-busy={busy ? true : undefined}
      aria-labelledby={targetId === undefined ? undefined : `${labelId} ${targetId}`}
      aria-describedby={described === '' ? undefined : described}
      onClick={() => {
        if (!disabled) onClick();
      }}
    >
      {tier === 'halt' ? (
        <svg className="tp-button__glyph" viewBox="0 0 10 10" aria-hidden="true">
          <rect x="1" y="1" width="8" height="8" fill="currentColor" />
        </svg>
      ) : null}
      <span id={labelId}>{label}</span>
    </button>
  );
}
