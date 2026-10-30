import type { ReactElement } from 'react';

/**
 * Text a person reads. A `CopyText` can only be made by the catalogue in
 * `copy/catalogue.ts` (its constructor is private there, and lint forbids the
 * type assertion elsewhere), and the design-system components accept nothing
 * else for their visible labels, so wording cannot drift into markup.
 *
 * This module holds types only; it exports no runtime value.
 */
declare const copyBrand: unique symbol;

export type CopyText = string & { readonly [copyBrand]: true };

/**
 * What a component may render: catalogue text, numbers, booleans, null and
 * undefined, JSX elements (whose own text lint checked where they were
 * written), and arrays, sets or promises of these. React's `ReactNode` also
 * admits any plain string, so a component that renders or forwards
 * `children` types them `CopyNode`; lint refuses everything else in a
 * rendered position (ADR-001 rule 5).
 */
export type CopyNode =
  CopyAwaited | Promise<CopyAwaited> | readonly CopyNode[] | ReadonlySet<CopyNode>;

/** A `CopyNode` that is not a promise: what a promise of copy may resolve to (React's AwaitedReactNode). */
export type CopyAwaited =
  | CopyText
  | number
  | bigint
  | boolean
  | null
  | undefined
  | ReactElement
  | readonly CopyAwaited[]
  | ReadonlySet<CopyAwaited>;

/** One catalogue entry: the short line always shown, and an optional "why" behind a disclosure. */
export interface CopyEntry {
  readonly short: CopyText;
  readonly why?: CopyText;
}
