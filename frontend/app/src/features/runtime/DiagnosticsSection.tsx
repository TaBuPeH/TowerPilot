import { useEffect } from 'react';
import { RUNTIME_FILTERS, runtimeHash } from '../../core/runtime.ts';
import { runtime as t } from '../../copy/runtime.ts';
import type { RuntimeStore } from '../../store/runtime.ts';
import { selectRuntime } from '../../store/selectors/runtime.ts';
import { useStoreSelector, viewEqual } from '../../store/store.ts';
import { Button } from '../../ui/Button.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { RegionBody } from '../../ui/Region.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Tile, Tiles } from '../../ui/Tile.tsx';
import { ShownList } from '../../ui/ShownList.tsx';
import { Fields, ReadState } from './IdentitySection.tsx';
import { RawData } from './RawData.tsx';

function download(runtime: RuntimeStore) {
  const json = runtime.exportJSON();
  if (json === null) return;
  const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'runtime-diagnostics.json';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

export function DiagnosticsSection({ runtime }: { readonly runtime: RuntimeStore }) {
  const vm = useStoreSelector(runtime, selectRuntime, viewEqual);
  // Visibility owns the timer. Reads neither announce nor change focus or native disclosure state.
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    const sync = () => {
      if (timer !== null) clearInterval(timer);
      timer =
        vm.auto && document.visibilityState === 'visible'
          ? setInterval(runtime.refreshEvents, 5000)
          : null;
    };
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => {
      if (timer !== null) clearInterval(timer);
      document.removeEventListener('visibilitychange', sync);
    };
  }, [runtime, vm.auto]);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const result = vm.diagnosticsVM;
  return (
    <RegionBody>
      <Tile
        titleId="runtime-query"
        title={t.diagnosticQuery.short}
        end={
          <HelpTip label={t.filters.short}>
            <p>{t.exactMatching.short}</p>
          </HelpTip>
        }
      >
        <details className="tp-runtime__filters" open>
          <summary>{t.filters.short}</summary>
          <div className="tp-runtime__filter-grid">
            {RUNTIME_FILTERS.map((key) => (
              <label key={key}>
                <span>{t.filterLabels[key].short}</span>
                <input
                  className="tp-field-control"
                  type="text"
                  value={vm.draft.exact[key] ?? ''}
                  onChange={(event) => {
                    runtime.edit(key, event.currentTarget.value);
                  }}
                />
              </label>
            ))}
            <label>
              <span>{t.since(zone)}</span>
              <input
                className="tp-field-control"
                type="datetime-local"
                step="0.001"
                value={vm.draft.since}
                aria-invalid={vm.problem === 'since' ? true : undefined}
                aria-describedby={vm.problem === 'since' ? 'runtime-filter-error' : undefined}
                onChange={(event) => {
                  runtime.edit('since', event.currentTarget.value);
                }}
              />
            </label>
            <label>
              <span>{t.limit.short}</span>
              <input
                className="tp-field-control"
                type="text"
                inputMode="numeric"
                value={vm.draft.limit}
                aria-invalid={vm.problem === 'limit' ? true : undefined}
                aria-describedby={vm.problem === 'limit' ? 'runtime-filter-error' : undefined}
                onChange={(event) => {
                  runtime.edit('limit', event.currentTarget.value);
                }}
              />
            </label>
          </div>
          <label className="tp-runtime__check">
            <input
              type="checkbox"
              checked={vm.draft.failures}
              onChange={(event) => {
                runtime.edit('failures', event.currentTarget.checked);
              }}
            />
            <span>{t.failures.short}</span>
          </label>
          <ActionRow
            reason={
              vm.problem === null ? null : (
                <Reason id="runtime-filter-error" entry={t.filterProblems[vm.problem]} />
              )
            }
          >
            <Button
              tier="secondary"
              label={t.apply.short}
              {...(vm.problem === null ? {} : { disabled: true, reasonId: 'runtime-filter-error' })}
              onClick={() => {
                const query = runtime.apply();
                if (query !== null) window.history.replaceState(null, '', runtimeHash(query));
              }}
            />
          </ActionRow>
        </details>
        <div className="tp-runtime__actions">
          <ActionRow
            reason={
              <>
                {result === null ? <Reason id="runtime-export-reason" entry={t.noExport} /> : null}
                {vm.events.busy ? <Reason id="runtime-events-busy" entry={t.busy} /> : null}
              </>
            }
          >
            <Button
              tier="secondary"
              label={t.refreshEvents.short}
              busy={vm.events.busy}
              {...(vm.events.busy ? { disabled: true, reasonId: 'runtime-events-busy' } : {})}
              onClick={runtime.refreshEvents}
            />
            <Button
              tier="quiet"
              label={t.export.short}
              {...(result === null ? { disabled: true, reasonId: 'runtime-export-reason' } : {})}
              onClick={() => {
                download(runtime);
              }}
            />
          </ActionRow>
        </div>
        <label className="tp-runtime__check">
          <input
            type="checkbox"
            checked={vm.auto}
            onChange={(event) => {
              runtime.setAuto(event.currentTarget.checked);
            }}
          />
          <span>{t.auto.short}</span>
        </label>
        <ReadState read={vm.events} error={vm.eventsError} />
      </Tile>
      {result === null ? null : (
        <>
          <Tiles min="wide">
            <Tile titleId="runtime-coverage" title={t.coverage.short}>
              <Fields fields={result.coverage} labels={t.coverageFields} />
              <details>
                <summary>{t.applied.short}</summary>
                <RawData label={t.applied.short} value={result.applied} />
              </details>
            </Tile>
            <Tile
              titleId="runtime-writer"
              title={t.writer.short}
              end={
                <HelpTip label={t.writer.short}>
                  <p>{t.writerNote.short}</p>
                </HelpTip>
              }
            >
              <Fields fields={result.writer} labels={t.writerFields} />
            </Tile>
          </Tiles>
          <Tile
            titleId="runtime-events"
            title={t.eventResults.short}
            end={
              <HelpTip label={t.eventResults.short}>
                <p>{t.failureNote.short}</p>
              </HelpTip>
            }
          >
            {result.rows.length === 0 ? (
              <p>{t.empty.short}</p>
            ) : (
              <ShownList
                items={result.rows}
                itemKey={(row) => row.key}
                more={t.moreEvents.short}
                className="tp-runtime__events"
                render={(row) => (
                  <div>
                    <details data-event-key={row.key}>
                      <summary>
                        <span className="tp-runtime__event-summary">
                          <span className="tp-runtime__event-value">
                            <span className="tp-runtime__muted">{t.eventKind.short}</span>
                            {row.kind === null ? (
                              t.notReported.short
                            ) : (
                              <ServerValue kind="detail" value={row.kind} />
                            )}
                          </span>
                          <span className="tp-runtime__event-value">
                            <span className="tp-runtime__muted">{t.eventTime.short}</span>
                            <span>{t.number(row.time)}</span>
                          </span>
                          <span>
                            {row.failure === 'failure'
                              ? t.failure.short
                              : row.failure === 'noFailure'
                                ? t.noFailure.short
                                : t.unknownFailure.short}
                          </span>
                        </span>
                      </summary>
                      <h4>{t.raw.short}</h4>
                      <RawData label={t.raw.short} value={row.raw} />
                    </details>
                    <Fields fields={row.evidence} labels={t.eventFields} />
                    <Fields
                      fields={row.ids.filter(
                        (field) => field.value !== null && field.key !== 'kind',
                      )}
                      labels={t.filterLabels}
                    />
                  </div>
                )}
              />
            )}
          </Tile>
        </>
      )}
    </RegionBody>
  );
}
