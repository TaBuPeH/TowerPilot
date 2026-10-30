import type { CopyNode } from '../copy/text.ts';
import './ui.css';

export interface FactsProps {
  /** `rows`: label column beside value column; `cells`: label over value, cells wrapping. */
  readonly layout?: 'rows' | 'cells' | 'inline';
  /** Feature hook classes, kept beside `tp-facts`. */
  readonly className?: string;
  /** `Fact` items. */
  readonly children: CopyNode;
}

/** Labelled values as a description list (`dl > div > dt + dd`), never a fused "label: value" line. */
export function Facts({ layout = 'rows', className, children }: FactsProps) {
  return (
    <dl
      className={className === undefined ? 'tp-facts' : `tp-facts ${className}`}
      data-layout={layout}
    >
      {children}
    </dl>
  );
}

export interface FactProps {
  readonly label: CopyNode;
  readonly value: CopyNode;
  /** The value's id, for a control that names or describes itself by it. */
  readonly id?: string;
}

/** One labelled value. */
export function Fact({ label, value, id }: FactProps) {
  return (
    <div className="tp-facts__item">
      <dt>{label}</dt>
      <dd id={id}>{value}</dd>
    </div>
  );
}
