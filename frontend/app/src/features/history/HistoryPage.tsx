import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { history as t } from '../../copy/history.ts';
import type { HistoryRead, HistoryStore } from '../../store/history.ts';
import { selectHistory, type HistoryRowVM } from '../../store/selectors/history.ts';
import { useStoreSelector, viewEqual } from '../../store/store.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../ui/Facts.tsx';
import { Region, RegionBody } from '../../ui/Region.tsx';
import { Tile } from '../../ui/Tile.tsx';
import { TopBar } from '../dashboard/topBar/TopBar.tsx';
import { HistoryDetail, HistoryText } from './HistoryDetail.tsx';
import './history.css';

function focusAnchor(id: string) {
  const heading = document.getElementById(id);
  heading?.focus();
  if (window.innerWidth <= 760) heading?.scrollIntoView({ block: 'start' });
}

function ReadState({ read }: { readonly read: HistoryRead }) {
  return (
    <div className="tp-history__read">
      {read.error === null ? null : (
        <p className="tp-history__notice">
          {t.errors[read.error].short} {t.unavailable.short}
        </p>
      )}
      {read.loadedAt === null ? null : (
        <p className="tp-history__note">
          <time>{t.loaded(read.loadedAt)}</time>
        </p>
      )}
    </div>
  );
}

function RecordRow({
  row,
  selected,
  onSelect,
}: {
  readonly row: HistoryRowVM;
  readonly selected: boolean;
  readonly onSelect: () => void;
}) {
  const reasonId = useId();
  return (
    <li>
      <button
        type="button"
        className="tp-button tp-history__record"
        data-tier={selected ? 'secondary' : 'quiet'}
        aria-pressed={selected}
        disabled={row.id === null}
        aria-describedby={row.id === null ? reasonId : undefined}
        onClick={onSelect}
      >
        <span className="tp-history__record-name">
          {row.name === null ? t.unnamed.short : <ServerValue kind="name" value={row.name} />}
        </span>
        <span className="tp-history__note tp-history__record-id">
          {row.recordId === null ? t.unknown.short : <ServerValue kind="id" value={row.recordId} />}
        </span>
        <span className="tp-history__record-start">
          <span className="tp-history__note">{t.start.short}</span>
          <time title={t.time(row.startedAt)}>{t.recordTime(row.startedAt)}</time>
        </span>
      </button>
      {row.id === null ? <Reason id={reasonId} entry={t.recordUnavailable} /> : null}
      <Facts layout="cells">
        <Fact label={t.status.short} value={<HistoryText value={row.status} />} />
        <Fact
          label={t.blueprint.short}
          value={
            row.blueprint === null ? (
              t.unknown.short
            ) : (
              <ServerValue kind="id" value={row.blueprint} />
            )
          }
        />
        <Fact label={t.tier.short} value={t.number(row.tier)} />
        <Fact label={t.observedWave.short} value={t.number(row.observedWave)} />
        <Fact label={t.finalWave.short} value={t.number(row.finalWave)} />
      </Facts>
    </li>
  );
}

/** Whether the element's content is taller than the box that holds it, kept current as either resizes. */
function useOverflowing<E extends HTMLElement>(watch: unknown) {
  const ref = useRef<E | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  useLayoutEffect(() => {
    const node = ref.current;
    if (node === null) return undefined;
    const measure = () => {
      setOverflowing(node.scrollHeight > node.clientHeight + 1);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    if (node.firstElementChild !== null) watcher.observe(node.firstElementChild);
    return () => {
      watcher.disconnect();
    };
  }, [watch]);
  return [ref, overflowing] as const;
}

/** H1–H4: list/detail inspector. Its only actions are scoped GET reads and local selection. */
export function HistoryPage({ history }: { readonly history: HistoryStore }) {
  const vm = useStoreSelector(history, selectHistory, viewEqual);
  const focusDetail = useRef(false);
  const announce = useAnnounce();
  const [scrollRef, scrolls] = useOverflowing<HTMLDivElement>(vm.rows);
  useEffect(() => {
    if (focusDetail.current && vm.selected !== null) {
      focusAnchor('history-detail');
      focusDetail.current = false;
    }
  }, [vm.selected]);
  useEffect(() => {
    // Completing a read may reveal enough content to scroll the anchor to the top.
    // Respect any focus move the person made while waiting, especially Back to records.
    if (
      vm.detailRead.loadedAt !== null &&
      document.activeElement?.id === 'history-detail' &&
      window.innerWidth <= 760
    )
      document.getElementById('history-detail')?.scrollIntoView({ block: 'start' });
  }, [vm.detailRead.loadedAt]);
  useEffect(() => {
    if (vm.list.error !== null) announce(t.errors[vm.list.error].short);
    else if (vm.list.loadedAt !== null) announce(t.recordsLoaded.short);
  }, [announce, vm.list.error, vm.list.loadedAt]);
  useEffect(() => {
    if (vm.detailRead.error !== null) announce(t.errors[vm.detailRead.error].short);
  }, [announce, vm.detailRead.error]);
  return (
    <>
      <header className="tp-topbar">
        <TopBar pageId="history" title={t.pageName.short} readOnly />
      </header>
      <main
        id="main"
        className="tp-main tp-history"
        data-detail={vm.selected === null ? 'none' : 'open'}
        tabIndex={-1}
      >
        <Region
          id="history-records-region"
          titleId="history-records"
          title={t.records.short}
          fallback={t.unavailable.short}
        >
          <RegionBody>
            <Tile titleId="history-filter" title={t.filtersAndPaging.short}>
              <ActionRow
                reason={
                  <>
                    {!vm.targetKnown ? (
                      <Reason id="history-target-reason" entry={t.targetUnknown} />
                    ) : null}
                    {vm.list.busy ? <Reason id="history-list-busy" entry={t.reading} /> : null}
                    {!vm.hasPrevious && !vm.list.busy ? (
                      <Reason id="history-previous-reason" entry={t.noPrevious} />
                    ) : null}
                    {!vm.hasNext && !vm.list.busy ? (
                      <Reason id="history-next-reason" entry={t.noNext} />
                    ) : null}
                  </>
                }
              >
                <div className="tp-history__toolbar tp-control-row">
                  <label>
                    <span>{t.filter.short}</span>
                    <select
                      className="tp-field-control"
                      value={vm.filter}
                      onChange={(event) => {
                        const filter = event.currentTarget.value;
                        if (
                          filter === 'all' ||
                          filter === 'active' ||
                          filter === 'prepared' ||
                          filter === 'ended' ||
                          filter === 'interrupted'
                        )
                          history.filter(filter);
                      }}
                    >
                      {Object.entries(t.filters).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label.short}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="tp-button"
                    data-tier="secondary"
                    type="button"
                    onClick={history.refreshList}
                    aria-disabled={vm.list.busy || !vm.targetKnown}
                    aria-describedby={
                      !vm.targetKnown
                        ? 'history-target-reason'
                        : vm.list.busy
                          ? 'history-list-busy'
                          : undefined
                    }
                  >
                    {vm.list.error === null ? t.refresh.short : t.retry.short}
                  </button>
                  <nav className="tp-control-row" aria-label={t.paging.short}>
                    <button
                      className="tp-button"
                      data-tier="quiet"
                      type="button"
                      disabled={!vm.hasPrevious || vm.list.busy}
                      aria-describedby={
                        vm.list.busy
                          ? 'history-list-busy'
                          : !vm.hasPrevious
                            ? 'history-previous-reason'
                            : undefined
                      }
                      onClick={() => {
                        history.page('previous');
                      }}
                    >
                      {t.previous.short}
                    </button>
                    <span>{t.page(vm.pageNumber)}</span>
                    <button
                      className="tp-button"
                      data-tier="quiet"
                      type="button"
                      disabled={!vm.hasNext || vm.list.busy}
                      aria-describedby={
                        vm.list.busy
                          ? 'history-list-busy'
                          : !vm.hasNext
                            ? 'history-next-reason'
                            : undefined
                      }
                      onClick={() => {
                        history.page('next');
                      }}
                    >
                      {t.next.short}
                    </button>
                  </nav>
                </div>
              </ActionRow>
            </Tile>
            <ReadState read={vm.list} />
            {vm.list.loadedAt !== null && vm.rows.length === 0 ? <p>{t.empty.short}</p> : null}
            {vm.selected === null ? <p className="tp-history__note">{t.choose.short}</p> : null}
            {scrolls ? (
              <p id="history-scroll-hint" className="tp-history__note">
                {t.scrollHint.short}
              </p>
            ) : null}
            <div
              ref={scrollRef}
              className="tp-history__table-scroll"
              data-scrolls={scrolls}
              aria-describedby={scrolls ? 'history-scroll-hint' : undefined}
              role="region"
              aria-label={t.recordTable.short}
              // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The bounded record list remains keyboard-scrollable.
              tabIndex={0}
            >
              <ul className="tp-history__records">
                {vm.rows.map((row, index) => (
                  <RecordRow
                    key={row.id ?? index}
                    row={row}
                    selected={row.id === vm.selected}
                    onSelect={() => {
                      if (row.id !== null) {
                        if (row.id === vm.selected) focusAnchor('history-detail');
                        else {
                          focusDetail.current = true;
                          history.select(row.id);
                        }
                      }
                    }}
                  />
                ))}
              </ul>
            </div>
          </RegionBody>
        </Region>
        {vm.selected === null ? null : (
          <Region
            id="history-detail-region"
            titleId="history-detail"
            title={t.detail.short}
            fallback={t.unavailable.short}
          >
            <RegionBody>
              <ActionRow
                reason={
                  vm.detailRead.busy ? (
                    <Reason id="history-detail-busy" entry={t.readingDetail} />
                  ) : null
                }
              >
                <button
                  className="tp-button"
                  data-tier="quiet"
                  type="button"
                  onClick={() => {
                    focusDetail.current = false;
                    focusAnchor('history-records');
                  }}
                >
                  {t.back.short}
                </button>
                <button
                  className="tp-button"
                  data-tier="secondary"
                  type="button"
                  aria-disabled={vm.detailRead.busy}
                  aria-describedby={vm.detailRead.busy ? 'history-detail-busy' : undefined}
                  onClick={history.refreshDetail}
                >
                  {vm.detailRead.error === null ? t.detailRefresh.short : t.detailRetry.short}
                </button>
              </ActionRow>
              <ReadState read={vm.detailRead} />
              {vm.detail === null ? null : (
                <HistoryDetail key={vm.selected} vm={vm.detail} onOlder={history.loadOlder} />
              )}
            </RegionBody>
          </Region>
        )}
      </main>
    </>
  );
}
