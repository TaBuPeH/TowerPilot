import type { CopyEntry } from '../../copy/text.ts';
import { runtime as t } from '../../copy/runtime.ts';
import type { RuntimeRead, RuntimeStore } from '../../store/runtime.ts';
import { selectRuntime, type RuntimeFields } from '../../store/selectors/runtime.ts';
import { useStoreSelector, viewEqual } from '../../store/store.ts';
import type { ServerText } from '../../copy/server.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { Button } from '../../ui/Button.tsx';
import { RegionBody } from '../../ui/Region.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { ShownList } from '../../ui/ShownList.tsx';
import { RawData } from './RawData.tsx';

const IDENTITY_DETAILS = [
  'source_fingerprint',
  'disk_source_fingerprint',
  'capabilities',
  'persistence_operations',
];

export function Fields({
  fields,
  labels,
  disclose = [],
}: {
  readonly fields: RuntimeFields;
  readonly labels: Readonly<Record<string, CopyEntry>>;
  readonly disclose?: readonly string[];
}) {
  if (fields.length === 0) return null;
  return (
    <Facts className="tp-runtime__facts">
      {fields.map((field) => (
        <Fact
          key={field.key}
          label={labels[field.key]?.short ?? t.notReported.short}
          value={
            field.value === null ? (
              t.notReported.short
            ) : disclose.includes(field.key) ? (
              <details>
                <summary>{t.fullValue.short}</summary>
                <ServerValue kind="detail" value={field.value} />
              </details>
            ) : (
              <ServerValue kind="detail" value={field.value} />
            )
          }
        />
      ))}
    </Facts>
  );
}
export function ReadState({
  read,
  error,
}: {
  readonly read: RuntimeRead;
  readonly error: ServerText<'message'> | null;
}) {
  return (
    <div className="tp-runtime__read">
      {read.busy ? <p>{t.loading.short}</p> : null}
      {read.error === null ? null : (
        <div className="tp-runtime__notice">
          <p>{read.loadedAt === null ? t.noRead.short : t.stale.short}</p>
          <p>{t[read.error].short}</p>
          {error === null ? null : (
            <p>
              <ServerValue kind="message" value={error} />
            </p>
          )}
        </div>
      )}
      {read.loadedAt === null ? null : (
        <p className="tp-runtime__muted">
          <time>{t.loaded(read.loadedAt)}</time>
        </p>
      )}
    </div>
  );
}
export function IdentitySection({ runtime }: { readonly runtime: RuntimeStore }) {
  const vm = useStoreSelector(runtime, selectRuntime, viewEqual);
  return (
    <RegionBody>
      <Tile
        titleId="runtime-source"
        title={t.source.short}
        end={
          <HelpTip label={t.source.short}>
            <p>{t.fingerprint.short}</p>
            {t.fingerprint.why === undefined ? null : <p>{t.fingerprint.why}</p>}
          </HelpTip>
        }
      >
        <ActionRow
          reason={vm.identity.busy ? <Reason id="runtime-identity-busy" entry={t.busy} /> : null}
        >
          <Button
            tier="secondary"
            label={t.refreshIdentity.short}
            busy={vm.identity.busy}
            {...(vm.identity.busy ? { disabled: true, reasonId: 'runtime-identity-busy' } : {})}
            onClick={runtime.refreshIdentity}
          />
        </ActionRow>
        <ReadState read={vm.identity} error={vm.identityError} />
        {vm.identityVM === null ? null : (
          <>
            <p data-source-state={vm.identityVM.source}>
              {t.sourceStates[vm.identityVM.source].short}
            </p>
            <Facts>
              <Fact label={t.snapshotAge.short} value={t.number(vm.identityVM.capturedAge)} />
            </Facts>
            <Fields
              fields={vm.identityVM.server}
              labels={t.serverFields}
              disclose={IDENTITY_DETAILS}
            />
            <Fields
              fields={vm.identityVM.runtime}
              labels={t.runtimeFields}
              disclose={IDENTITY_DETAILS}
            />
          </>
        )}
      </Tile>
      {vm.identityVM === null ? null : (
        <>
          <Tile titleId="runtime-process" title={t.process.short}>
            <Fields fields={vm.identityVM.process} labels={t.processFields} />
          </Tile>
          <Tile
            titleId="runtime-workers"
            title={t.workers.short}
            end={
              <HelpTip label={t.workers.short}>
                <p>{t.workerSafety.short}</p>
              </HelpTip>
            }
          >
            {vm.identityVM.workers.length === 0 ? (
              <p>{t.noWorkers.short}</p>
            ) : (
              <ShownList
                items={vm.identityVM.workers}
                itemKey={(worker) => worker.key}
                more={t.moreWorkers.short}
                className="tp-runtime__workers"
                render={(worker) => (
                  <>
                    <p data-worker-alive={worker.alive}>{t.aliveStates[worker.alive].short}</p>
                    <Fields
                      fields={worker.fields}
                      labels={t.workerFields}
                      disclose={IDENTITY_DETAILS}
                    />
                  </>
                )}
              />
            )}
          </Tile>
          <Tile titleId="runtime-raw-identity" title={t.retainedEvidence.short}>
            <details>
              <summary>{t.rawIdentity.short}</summary>
              <RawData label={t.rawIdentity.short} value={vm.identityVM.raw} />
            </details>
          </Tile>
        </>
      )}
    </RegionBody>
  );
}
