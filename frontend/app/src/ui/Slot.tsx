import type { ErrorInfo } from 'react';
import type { CopyNode, CopyText } from '../copy/text.ts';
import { RegionBoundary } from './RegionBoundary.tsx';
import './ui.css';

export interface SlotProps {
  /** automation, input or gem. */
  readonly id: string;
  readonly label: CopyText;
  /** The slot's body: `SlotFacts`, `SlotAction` and `SlotMeta` rows. */
  readonly children: CopyNode;
  /** The rows drawn when the body throws; always with the slot's revocation (S-091). */
  readonly fallback: CopyNode;
  readonly resetKey?: string;
  readonly onError?: (error: unknown, info: ErrorInfo) => void;
}

/**
 * One fixed Control slot (spec 2.2, 3.5): label, facts, action and meta rows on
 * a grid shared by the three slots, so the three revocation buttons line up; at
 * narrow widths the action sits on the label's line. The body has its own
 * boundary: a throw draws the fallback rows, which keep the revocation.
 */
export function Slot({ id, label, children, fallback, resetKey, onError }: SlotProps) {
  return (
    <section className="tp-slot" data-slot={id} aria-labelledby={`slot-${id}-label`}>
      <h3 id={`slot-${id}-label`} className="tp-slot__label">
        {label}
      </h3>
      <RegionBoundary
        fallback={fallback}
        {...(resetKey === undefined ? {} : { resetKey })}
        {...(onError === undefined ? {} : { onError })}
      >
        {children}
      </RegionBoundary>
    </section>
  );
}

/** The facts row: pill, chip and lines. Holds no control, so tab order follows the action row. */
export function SlotFacts({ children }: { readonly children: CopyNode }) {
  return <div className="tp-slot__facts">{children}</div>;
}

/** The action row: the slot's revocation (and its siblings). */
export function SlotAction({ children }: { readonly children: CopyNode }) {
  return <div className="tp-slot__action">{children}</div>;
}

/** The meta row: Why, Details. */
export function SlotMeta({ children }: { readonly children: CopyNode }) {
  return <div className="tp-slot__meta">{children}</div>;
}
