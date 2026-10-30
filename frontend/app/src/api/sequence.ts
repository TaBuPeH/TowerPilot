/**
 * The page's request sequence (safety rules: "Later read"): one monotonic
 * counter shared by the poller and the write-intent machine. A read takes a
 * point when it STARTS and another when it arrives; a write takes one when it
 * is sent and one when its answer arrives. Core compares these points
 * (`startedSeq`, `sentSeq`, `answerSeq`, `learnedSeq`), never browser clocks.
 */
export interface Sequence {
  readonly next: () => number;
  readonly current: () => number;
}

export function createSequence(start = 0): Sequence {
  let value = start;
  return {
    next: () => {
      value += 1;
      return value;
    },
    current: () => value,
  };
}
