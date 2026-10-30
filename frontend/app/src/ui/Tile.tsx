import type { CopyEntry, CopyNode } from '../copy/text.ts';
import { CopyLine } from './CopyLine.tsx';
import './ui.css';

export interface TileProps {
  /** The title's id: the tile is named by it, and focus can land on it (tabIndex -1). */
  readonly titleId: string;
  /** Catalogue text, or a ServerValue name. */
  readonly title: CopyNode;
  /** `section` (default) titles with a heading; `fieldset` titles with a legend, for form fields. */
  readonly as?: 'section' | 'fieldset';
  /** A section tile's heading: 3 inside a Region (default), 4 inside a section tile. */
  readonly level?: 3 | 4;
  /** One short line under the title, its "why" behind the Why disclosure. */
  readonly intro?: CopyEntry;
  /** The header's end: a chip, a count or a link. Drawn after the title, so it follows it in tab order. */
  readonly end?: CopyNode;
  readonly density?: 'default' | 'compact';
  /** Attention draws the one edge a tile may have. */
  readonly tone?: 'default' | 'attention';
  /** Feature hook classes, kept beside `tp-tile`. */
  readonly className?: string;
  readonly children: CopyNode;
}

function classes(base: string, extra: string | undefined): string {
  return extra === undefined ? base : `${base} ${extra}`;
}

/**
 * A titled group inside a Region (surface grammar, council-tiles synthesis): a
 * borderless fill with a smaller radius; a tile inside a tile collapses to a
 * divider (src/ui/ui.css). It has no error boundary of its own: boundaries stay
 * with the parts that own them.
 */
export function Tile({
  titleId,
  title,
  as = 'section',
  level = 3,
  intro,
  end,
  density = 'default',
  tone = 'default',
  className,
  children,
}: TileProps) {
  const data = {
    'data-density': density === 'default' ? undefined : density,
    'data-tone': tone === 'default' ? undefined : tone,
  };
  const endPart = end === undefined ? null : <div className="tp-tile__end">{end}</div>;
  const introPart = intro === undefined ? null : <CopyLine entry={intro} variant="intro" />;
  const body = <div className="tp-tile__body">{children}</div>;
  if (as === 'fieldset') {
    return (
      <fieldset className={classes('tp-tile', className)} {...data}>
        <legend id={titleId} className="tp-tile__title" tabIndex={-1}>
          {title}
        </legend>
        {endPart}
        {introPart}
        {body}
      </fieldset>
    );
  }
  const Heading = level === 4 ? 'h4' : 'h3';
  return (
    <section className={classes('tp-tile', className)} aria-labelledby={titleId} {...data}>
      <div className="tp-tile__head">
        <Heading id={titleId} className="tp-tile__title" tabIndex={-1}>
          {title}
        </Heading>
        {endPart}
      </div>
      {introPart}
      {body}
    </section>
  );
}

export interface TilesProps {
  /** The narrowest a tile may get before the row wraps: 12rem, 18rem (default) or 26rem. */
  readonly min?: 'narrow' | 'default' | 'wide';
  readonly children: CopyNode;
}

/** Tiles side by side, wrapping to fewer columns as the region narrows. */
export function Tiles({ min = 'default', children }: TilesProps) {
  return (
    <div className="tp-tiles" data-min={min === 'default' ? undefined : min}>
      {children}
    </div>
  );
}
