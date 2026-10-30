import { copy } from '../copy/catalogue.ts';
import type { CopyText } from '../copy/text.ts';
import './ui.css';

/** The eight status words (spec 3.1); the word comes from the catalogue, never from the caller. */
export type ChipState = keyof typeof copy.states;

export interface ChipProps {
  readonly state: ChipState;
  /** The age of the evidence or the last read ("2 s", "last read 1 m ago"). */
  readonly age?: CopyText;
  /** For aria-labelledby or aria-describedby from a control. */
  readonly id?: string;
  /** A catalogue word that names this state more exactly ("Connected", "Delivery unknown"). */
  readonly label?: CopyText;
}

/** Each word's shape (spec 3.1), drawn in code and hidden from assistive technology. */
function Shape({ state }: { readonly state: ChipState }) {
  const common = {
    className: 'tp-chip__shape',
    viewBox: '0 0 12 12',
    'aria-hidden': true,
  } as const;
  switch (state) {
    case 'running':
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="5" fill="currentColor" />
        </svg>
      );
    case 'waiting':
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M6 1.5 A4.5 4.5 0 0 1 10.5 6 L6 6 Z" fill="currentColor" />
        </svg>
      );
    case 'paused':
      return (
        <svg {...common}>
          <rect x="2.5" y="2" width="2.5" height="8" fill="currentColor" />
          <rect x="7" y="2" width="2.5" height="8" fill="currentColor" />
        </svg>
      );
    case 'idle':
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case 'stopping':
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="5" fill="none" stroke="currentColor" strokeWidth="1.2" />
          <rect x="4" y="4" width="4" height="4" fill="currentColor" />
        </svg>
      );
    case 'unknown':
      return (
        <svg {...common}>
          <path d="M6 0.8 L11.2 6 L6 11.2 L0.8 6 Z" fill="none" stroke="currentColor" />
          <path
            d="M4.6 4.6 A1.5 1.5 0 1 1 6.6 6 Q6 6.3 6 7.2"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.1"
          />
          <circle cx="6" cy="8.8" r="0.7" fill="currentColor" />
        </svg>
      );
    case 'blocked':
      return (
        <svg {...common}>
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M2.8 9.2 L9.2 2.8" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case 'failed':
      return (
        <svg {...common}>
          <path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" stroke="currentColor" strokeWidth="1.8" />
        </svg>
      );
  }
}

/**
 * A status chip (spec 3.1): one of the eight states as its shape and colour, named
 * by its word or by a more exact catalogue label ("Connected", "Delivery unknown"). Colour is never the only signal, and Unknown never looks like Off.
 */
export function Chip({ state, age, id, label }: ChipProps) {
  return (
    <span className="tp-chip" data-state={state} id={id}>
      <Shape state={state} />
      <span className="tp-chip__word">{label ?? copy.states[state].short}</span>
      {age === undefined ? null : <span className="tp-chip__age">{age}</span>}
    </span>
  );
}
