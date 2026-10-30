import { history as t } from '../../copy/history.ts';
import type { ServerText } from '../../copy/server.ts';
import type { CopyText } from '../../copy/text.ts';
import type { HistoryDetailVM } from '../../store/selectors/history.ts';
import { Facts, Fact } from '../../ui/Facts.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { Disclosure } from '../../ui/Disclosure.tsx';
import { ActionExecutions } from './ActionExecutions.tsx';
import { ServerValue } from '../../ui/ServerValue.tsx';

export function HistoryText({ value }: { readonly value: ServerText<'detail'> | null }) {
  return value === null ? t.unknown.short : <ServerValue kind="detail" value={value} />;
}
function Evidence({
  value,
  label,
}: {
  readonly value: ServerText<'detail'> | null;
  readonly label: CopyText;
}) {
  if (value === null) return <p>{t.unknown.short}</p>;
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Scrollable evidence needs keyboard focus (WCAG 2.1.1).
    <pre tabIndex={0} role="region" aria-label={label}>
      <ServerValue kind="detail" value={value} />
    </pre>
  );
}

/** Presentation of checked historical evidence. No collection arithmetic or outcome inference. */
export function HistoryDetail({
  vm,
  onOlder,
}: {
  readonly vm: HistoryDetailVM;
  readonly onOlder: () => void;
}) {
  return (
    <div className="tp-history__evidence">
      <Tile
        titleId="history-summary"
        title={vm.name === null ? t.unnamed.short : <ServerValue kind="name" value={vm.name} />}
      >
        {vm.status === 'interrupted' ? (
          <p className="tp-history__notice">{t.interrupted.short}</p>
        ) : null}
        {vm.status === 'prepared' ? <p className="tp-history__notice">{t.prepared.short}</p> : null}
        {vm.partial ? <p className="tp-history__notice">{t.partial.short}</p> : null}
        <Facts layout="cells" className="tp-history__facts">
          <Fact
            label={t.id.short}
            value={
              vm.recordId === null ? t.unknown.short : <ServerValue kind="id" value={vm.recordId} />
            }
          />
          <Fact label={t.status.short} value={<HistoryText value={vm.status} />} />
          <Fact label={t.result.short} value={<HistoryText value={vm.result} />} />
          <Fact
            label={t.reason.short}
            value={
              vm.reason === null ? (
                t.unknown.short
              ) : (
                <ServerValue kind="message" value={vm.reason} />
              )
            }
          />
          <Fact label={t.origin.short} value={<HistoryText value={vm.origin} />} />
          <Fact label={t.certainty.short} value={<HistoryText value={vm.certainty} />} />
          <Fact label={t.tier.short} value={t.number(vm.tier)} />
          <Fact label={t.observedWave.short} value={t.number(vm.observedWave)} />
          <Fact label={t.finalWave.short} value={t.number(vm.finalWave)} />
          <Fact label={t.provenance.short} value={<HistoryText value={vm.provenance} />} />
          <Fact label={t.start.short} value={t.time(vm.startedAt)} />
          <Fact label={t.observedStart.short} value={t.time(vm.observedAt)} />
          <Fact label={t.ended.short} value={t.time(vm.endedAt)} />
          <Fact label={t.updated.short} value={t.time(vm.updatedAt)} />
          <Fact label={t.duration.short} value={t.seconds(vm.duration)} />
          <Fact label={t.observation.short} value={<HistoryText value={vm.observation} />} />
          <Fact
            label={t.previousId.short}
            value={
              vm.previousId === null ? (
                t.unknown.short
              ) : (
                <ServerValue kind="id" value={vm.previousId} />
              )
            }
          />
        </Facts>
      </Tile>
      <Tile titleId="history-setup" title={t.setup.short}>
        <Facts layout="cells" className="tp-history__facts">
          <Fact label={t.setupState.short} value={<HistoryText value={vm.setup.state} />} />
          <Fact label={t.setupDetail.short} value={<HistoryText value={vm.setup.detail} />} />
          <Fact
            label={t.setupReason.short}
            value={
              vm.setup.reason === null ? (
                t.unknown.short
              ) : (
                <ServerValue kind="message" value={vm.setup.reason} />
              )
            }
          />
          <Fact
            label={t.loadout.short}
            value={
              vm.setup.loadout === null ? (
                t.unknown.short
              ) : (
                <ServerValue kind="name" value={vm.setup.loadout} />
              )
            }
          />
        </Facts>
        {vm.setup.partial ? <p>{t.setupPartial.short}</p> : null}
        <Disclosure label={t.modules.short}>
          <Evidence value={vm.setup.modules} label={t.moduleRecord.short} />
        </Disclosure>
      </Tile>
      <Tile titleId="history-stats" title={t.stats.short}>
        <p>{t.statsStates[vm.stats.state].short}</p>
        <Facts layout="cells" className="tp-history__facts">
          <Fact
            label={t.statsRecordedState.short}
            value={<HistoryText value={vm.stats.recordedState} />}
          />
          {vm.stats.reason === null ? null : (
            <Fact
              label={t.statsReason.short}
              value={<ServerValue kind="message" value={vm.stats.reason} />}
            />
          )}
          {vm.stats.error === null ? null : (
            <Fact
              label={t.statsError.short}
              value={<ServerValue kind="message" value={vm.stats.error} />}
            />
          )}
        </Facts>
        <Disclosure label={t.rawEvidence.short}>
          <Evidence value={vm.stats.raw} label={t.statsRecord.short} />
        </Disclosure>
      </Tile>
      <Tile titleId="history-collection" title={t.collection.short}>
        <p>{t.collectionStates[vm.collection.state].short}</p>
        <p className="tp-history__note">{t.sampled.short}</p>
        <Facts layout="cells" className="tp-history__facts">
          <Fact label={t.claims.short} value={t.number(vm.collection.claims)} />
          <Fact label={t.balances.short} value={t.number(vm.collection.balances)} />
          <Fact label={t.increase.short} value={t.number(vm.collection.increase)} />
          <Fact label={t.from.short} value={t.time(vm.collection.startedAt)} />
          <Fact label={t.through.short} value={t.time(vm.collection.throughAt)} />
        </Facts>
        <Disclosure label={t.rawEvidence.short}>
          <Evidence value={vm.collectionRaw} label={t.collectionRecord.short} />
        </Disclosure>
      </Tile>
      <ActionExecutions vm={vm.executions} onOlder={onOlder} />
      <Tile titleId="history-gaps" title={t.gaps.short}>
        <p className="tp-history__note">{t.gapNote.short}</p>
        <Facts layout="cells" className="tp-history__facts">
          <Fact label={t.openGap.short} value={t.time(vm.openGap)} />
        </Facts>
        <Evidence value={vm.gaps} label={t.gapRecord.short} />
      </Tile>
      <Tile titleId="history-raw-evidence" title={t.rawEvidence.short}>
        <Disclosure label={t.boundary.short}>
          <Evidence value={vm.boundary} label={t.boundary.short} />
        </Disclosure>
        <Disclosure label={t.snapshot.short}>
          {vm.snapshot === null ? (
            <p>{t.noSnapshot.short}</p>
          ) : (
            <Evidence value={vm.snapshot} label={t.snapshot.short} />
          )}
          <h3>{t.effective.short}</h3>
          <Evidence value={vm.effective} label={t.effective.short} />
        </Disclosure>
        <Disclosure label={t.activity.short}>
          {vm.events.length === 0 ? (
            <p>{t.noActivity.short}</p>
          ) : (
            <ol className="tp-history__activity">
              {vm.events.map((event, index) => (
                <li key={event.key}>
                  <time>{t.time(event.at)}</time>
                  <HistoryText value={event.kind} />
                  <Evidence value={event.raw} label={t.eventRecord(index + 1)} />
                </li>
              ))}
            </ol>
          )}
        </Disclosure>
      </Tile>
    </div>
  );
}
