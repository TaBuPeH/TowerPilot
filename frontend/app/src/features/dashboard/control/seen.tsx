import { copy } from '../../../copy/catalogue.ts';
import { lastRead } from '../../../copy/dashboard/shell.ts';
import type { CopyEntry, CopyText } from '../../../copy/text.ts';
import type { ChipVM } from '../../../store/viewModels.ts';
import { Chip } from '../../../ui/Chip.tsx';

/**
 * A chip line of a Control slot: the word (when the row has one), the line, extra
 * lines. A line that only names the chip's state more exactly is the chip's label
 * instead (`chipLabel`, `line` null), so the state is said once.
 */
export function SeenLine({
  chip,
  line,
  prefix,
  extra,
  chipLabel,
}: {
  readonly chip: ChipVM;
  readonly line: CopyText | null;
  readonly prefix?: CopyText;
  readonly extra?: CopyText | null;
  readonly chipLabel?: CopyText;
}) {
  const stateChip =
    chip.word === null ? null : (
      <Chip state={chip.word} {...(chipLabel === undefined ? {} : { label: chipLabel })} />
    );
  return (
    <p className="tp-seen" data-row={chip.row} data-evidence={chip.evidence ?? undefined}>
      {prefix === undefined ? (
        stateChip
      ) : (
        <span className="tp-seen__state">
          <span className="tp-seen__prefix">{prefix}</span>
          {stateChip}
        </span>
      )}
      {line === null ? null : <span className="tp-seen__line">{line}</span>}
      {extra === null || extra === undefined ? null : (
        <span className="tp-seen__extra">{extra}</span>
      )}
    </p>
  );
}

/** A number param of a chip, or null. */
export function numberParam(chip: ChipVM, name: string): number | null {
  const value = chip.params[name];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** A string-list param of a chip (never shown as text: codes only). */
export function listParam(chip: ChipVM, name: string): readonly string[] {
  const value = chip.params[name];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

/** A code param of a chip, or null. */
export function codeParam(chip: ChipVM, name: string): string | null {
  const value = chip.params[name];
  return typeof value === 'string' ? value : null;
}

/** The entry for a code, or a fallback entry; never shows the code itself. */
export function entryFor(
  table: Readonly<Record<string, CopyEntry>>,
  code: string | null,
  fallback: CopyEntry,
): CopyEntry {
  return code === null ? fallback : (table[code] ?? fallback);
}

/** "last read {age}" for a row that reports a stale read (G2, A1). */
export function staleLine(chip: ChipVM): CopyText | null {
  const age = numberParam(chip, 'ageSec');
  return age === null ? null : lastRead(age);
}

export const shellWords = copy.dashboard.shell;
