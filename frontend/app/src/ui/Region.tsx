import type { ErrorInfo } from 'react';
import type { CopyNode } from '../copy/text.ts';
import { RegionBoundary } from './RegionBoundary.tsx';
import './ui.css';

export interface RegionProps {
  /** Stable id: the heading is `${id}-title`, focusable for focus after removal (spec 8.11). */
  readonly id: string;
  /** The heading's id when a page already focuses or links to one (default `${id}-title`). */
  readonly titleId?: string;
  readonly title: CopyNode;
  /** Drawn in the body area when the region's content throws; keeps its revocations (S-091). */
  readonly fallback: CopyNode;
  readonly resetKey?: string;
  readonly onError?: (error: unknown, info: ErrorInfo) => void;
  /** `RegionCount`, `RegionEnd` and `RegionBody` parts, all inside the boundary. */
  readonly children: CopyNode;
}

/**
 * A card with an h2 and everything else inside its own error boundary (spec
 * 3.5, S-090): the heading is static catalogue text, so a throw in the
 * region's view model replaces only the count, the header's end and the body.
 */
export function Region({ id, titleId, title, fallback, resetKey, onError, children }: RegionProps) {
  const heading = titleId ?? `${id}-title`;
  return (
    <section className="tp-region" data-region={id} aria-labelledby={heading}>
      <h2 id={heading} className="tp-region__title" tabIndex={-1}>
        {title}
      </h2>
      <RegionBoundary
        fallback={<div className="tp-region__body">{fallback}</div>}
        {...(resetKey === undefined ? {} : { resetKey })}
        {...(onError === undefined ? {} : { onError })}
      >
        {children}
      </RegionBoundary>
    </section>
  );
}

/** The count beside the title ("Needs you 1"). */
export function RegionCount({ value }: { readonly value: number }) {
  return <span className="tp-region__count">{value}</span>;
}

/** The header's end: a scope line, a chip or a link. */
export function RegionEnd({ children }: { readonly children: CopyNode }) {
  return <div className="tp-region__end">{children}</div>;
}

/** A short body on the title's line (an empty region in one line, F4-02); it wraps when narrow. */
export function RegionInline({ children }: { readonly children: CopyNode }) {
  return <div className="tp-region__inline">{children}</div>;
}

/** The region's body. */
export function RegionBody({ children }: { readonly children: CopyNode }) {
  return <div className="tp-region__body">{children}</div>;
}
