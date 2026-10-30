import { useEffect, useRef } from 'react';
import { actions as shared } from '../../copy/actions.ts';
import { copy } from '../../copy/catalogue.ts';
import { history as t } from '../../copy/history.ts';
import type { ServerText } from '../../copy/server.ts';
import type { CopyEntry } from '../../copy/text.ts';
import type { HistoryDetailVM } from '../../store/selectors/history.ts';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Facts, Fact } from '../../ui/Facts.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';

type ExecutionsVM = HistoryDetailVM['executions'];
type RowVM = ExecutionsVM['rows'][number];

interface CodedVM<K extends string> {
  readonly kind: K | 'other' | 'unreported';
  readonly code: ServerText<'detail'> | null;
}

/** A server word through its catalogue table; a word this build does not know shows as sent. */
function Word<K extends string>({
  word,
  words,
}: {
  readonly word: CodedVM<K>;
  readonly words: Readonly<Record<K, CopyEntry>>;
}) {
  if (word.kind === 'unreported') return t.unknown.short;
  if (word.kind === 'other')
    return (
      <>
        {t.actions.notRecognised.short}
        {word.code === null ? null : (
          <>
            {t.separator.short}
            <ServerValue kind="detail" value={word.code} />
          </>
        )}
      </>
    );
  return words[word.kind].short;
}

/** The action's label, or its code in plain monospace when the server sent no label (Q4). */
function ActionName({ row }: { readonly row: RowVM }) {
  if (row.label !== null) return <ServerValue kind="name" value={row.label} />;
  if (row.action === null) return t.unknown.short;
  return (
    <code className="tp-history__code">
      <ServerValue kind="id" value={row.action} />
    </code>
  );
}

function Finished({ row }: { readonly row: RowVM }) {
  if (row.finishedAt !== null) return t.time(row.finishedAt);
  if (row.open) return t.actions.notFinished.short;
  if (row.state.kind === 'interrupted') return t.actions.noFinish.short;
  return t.unknown.short;
}

function Execution({
  row,
  readAtMs,
  anchor,
}: {
  readonly row: RowVM;
  readonly readAtMs: number | null;
  readonly anchor: (element: HTMLLIElement | null) => void;
}) {
  return (
    <li ref={anchor} tabIndex={-1} className="tp-history__execution">
      <div className="tp-history__execution-head">
        <strong>
          <ActionName row={row} />
        </strong>
        <time>{t.time(row.startedAt)}</time>
      </div>
      <Facts layout="cells" className="tp-history__facts">
        <Fact
          label={t.actions.state.short}
          value={
            <>
              <Word word={row.state} words={t.actions.stateWords} />
              {row.open ? (
                <span className="tp-history__note tp-history__block">
                  {t.actions.readAt(readAtMs)}
                </span>
              ) : null}
            </>
          }
        />
        {row.result === null ? null : (
          <Fact
            label={t.actions.result.short}
            value={<Word word={row.result} words={t.actions.stateWords} />}
          />
        )}
        {row.effect === null ? null : (
          <Fact
            label={t.actions.effect.short}
            value={<Word word={row.effect} words={shared.effects} />}
          />
        )}
        <Fact
          label={t.actions.trigger.short}
          value={<Word word={row.trigger} words={t.actions.triggers} />}
        />
        <Fact label={t.actions.finished.short} value={<Finished row={row} />} />
        {row.showsDuration ? (
          <Fact
            label={t.actions.duration.short}
            value={
              row.durationMs === null
                ? t.actions.noDuration.short
                : t.actions.durationValue(row.durationMs)
            }
          />
        ) : null}
        {row.aggregate === null ? null : (
          <>
            <Fact label={t.actions.checks.short} value={t.number(row.aggregate.count)} />
            <Fact label={t.actions.firstCheck.short} value={t.time(row.aggregate.firstAt)} />
            <Fact label={t.actions.lastCheck.short} value={t.time(row.aggregate.lastAt)} />
          </>
        )}
        <Fact
          label={t.actions.coverage.short}
          value={<Word word={row.coverage} words={t.actions.coverages} />}
        />
        {row.label === null || row.action === null ? null : (
          <Fact
            label={t.actions.code.short}
            value={
              <code className="tp-history__code">
                <ServerValue kind="id" value={row.action} />
              </code>
            }
          />
        )}
        {row.instance === null ? null : (
          <Fact
            label={t.actions.instance.short}
            value={
              <code className="tp-history__code">
                <ServerValue kind="id" value={row.instance} />
              </code>
            }
          />
        )}
      </Facts>
      {row.reason === null ? null : (
        <Disclosure label={copy.common.why.short}>
          <ServerValue kind="message" value={row.reason} />
        </Disclosure>
      )}
    </li>
  );
}

/**
 * A2-7 AC4: the executions of timed actions recorded for the selected battle. Presentation of the
 * words core/historyActions.ts and the store selector decided; it computes nothing. The one action
 * is the "Older executions" read, which the store runs.
 */
export function ActionExecutions({
  vm,
  onOlder,
}: {
  readonly vm: ExecutionsVM;
  readonly onOlder: () => void;
}) {
  const announce = useAnnounce();
  const rowElements = useRef<(HTMLLIElement | null)[]>([]);
  const asked = useRef<number | null>(null);
  const wasBusy = useRef(false);
  const doneNote = useRef<HTMLParagraphElement>(null);
  const { rows } = vm;
  const { busy, error: failed } = vm.older;
  // A new read of the detail replaces the rows, so an ask made before it no longer applies.
  useEffect(() => {
    asked.current = null;
  }, [vm.readAtMs]);
  useEffect(() => {
    const was = wasBusy.current;
    wasBusy.current = busy;
    const before = asked.current;
    if (before === null || !was || busy || failed !== null) return;
    asked.current = null;
    const added = rows.length - before;
    announce(added > 0 ? t.actions.olderLoaded(added) : t.actions.complete.short);
    // The button leaves with the last page; keep the person's place at the first older row,
    // or at the completion note when the page added none.
    if (!vm.canLoadOlder) (added > 0 ? rowElements.current[before] : doneNote.current)?.focus();
  }, [announce, busy, failed, rows.length, vm.canLoadOlder]);
  const failure = vm.older.error;
  useEffect(() => {
    if (failure === null) return;
    asked.current = null;
    announce(t.errors[failure].short);
  }, [announce, failure]);
  return (
    <Tile titleId="history-actions" title={t.actions.heading.short}>
      {vm.state === 'rows' ? (
        <>
          <p className="tp-history__note">{t.actions.readNote.short}</p>
          <ol className="tp-history__executions" aria-label={t.actions.list.short}>
            {rows.map((row, index) => (
              <Execution
                key={row.key}
                row={row}
                readAtMs={vm.readAtMs}
                anchor={(element) => {
                  rowElements.current[index] = element;
                }}
              />
            ))}
          </ol>
          {vm.skipped > 0 ? (
            <p className="tp-history__note">{t.actions.skipped(vm.skipped)}</p>
          ) : null}
          {vm.truncated ? (
            <p className="tp-history__notice">
              {vm.canLoadOlder ? t.actions.truncated.short : t.actions.truncatedNoCursor.short}
            </p>
          ) : null}
          {vm.loadedOlder && !vm.truncated ? (
            <p ref={doneNote} tabIndex={-1} className="tp-history__note">
              {t.actions.complete.short}
            </p>
          ) : null}
          {vm.canLoadOlder ? (
            <ActionRow
              reason={
                vm.older.busy ? (
                  <Reason id="history-older-busy" entry={t.actions.olderPending} />
                ) : null
              }
            >
              <button
                type="button"
                className="tp-button tp-history__older"
                data-tier="secondary"
                aria-disabled={vm.older.busy}
                aria-describedby={vm.older.busy ? 'history-older-busy' : undefined}
                onClick={() => {
                  if (vm.older.busy) return;
                  asked.current = rows.length;
                  onOlder();
                }}
              >
                {vm.older.busy
                  ? t.actions.olderBusy.short
                  : vm.older.error === null
                    ? t.actions.older.short
                    : t.actions.olderRetry.short}
              </button>
            </ActionRow>
          ) : null}
          {vm.older.error === null ? null : (
            <p className="tp-history__notice">
              {t.errors[vm.older.error].short} {t.unavailable.short}
            </p>
          )}
          {vm.older.reason === null ? null : (
            <Disclosure label={copy.common.why.short}>
              <ServerValue
                kind="detail"
                value={vm.older.reason}
                label={t.actions.reasonCode.short}
              />
            </Disclosure>
          )}
        </>
      ) : (
        <>
          <p>{t.actions.states[vm.state].short}</p>
          {vm.state === 'unreadable' && vm.reason !== null ? (
            <Disclosure label={copy.common.why.short}>
              <ServerValue kind="detail" value={vm.reason} label={t.actions.reasonCode.short} />
            </Disclosure>
          ) : null}
        </>
      )}
    </Tile>
  );
}
