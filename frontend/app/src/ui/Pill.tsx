import type { CopyText } from '../copy/text.ts';
import './ui.css';

export interface PillProps {
  /** "Your choice:" or "Setting:". */
  readonly prefix: CopyText;
  readonly value: CopyText;
  /** "last read {age}" when the value is kept from an earlier read. */
  readonly age?: CopyText;
  readonly id?: string;
}

/** A setting pill (spec 3.2): square, neutral, no dot; desired never looks like observed. */
export function Pill({ prefix, value, age, id }: PillProps) {
  return (
    <span className="tp-pill" id={id}>
      <span className="tp-pill__prefix">{prefix}</span>
      <span className="tp-pill__value">{value}</span>
      {age === undefined ? null : <span className="tp-pill__age">{age}</span>}
    </span>
  );
}
