import { recognitionAdvanced as t } from '../../../copy/recognitionAdvanced.ts';
import type {
  RecognitionAdvancedState,
  RecognitionAdvancedStore,
} from '../../../store/recognitionAdvanced.ts';
import { selectAdvanced } from '../../../store/selectors/recognitionAdvanced.ts';
import { ActionRow, Reason } from '../../../ui/ActionRow.tsx';
import { Fact, Facts } from '../../../ui/Facts.tsx';
import { ShownList } from '../../../ui/ShownList.tsx';
import { Button } from '../../../ui/Button.tsx';
import { Coordinates, Data, Values } from './Data.tsx';
export function Analysis({
  store,
  state,
  now,
}: {
  readonly store: RecognitionAdvancedStore;
  readonly state: RecognitionAdvancedState;
  readonly now: number;
}) {
  const vm = selectAdvanced(state, now);
  const target = vm.target;
  const a = vm.analysis;
  const blocked = state.context === null || target?.checking === true || vm.launchPending;
  const answer = vm.launch;
  return (
    <>
      <p>{t.observation.short}</p>
      <ActionRow
        reason={
          <>
            {blocked ? (
              <Reason
                id="analysis-blocked"
                entry={
                  state.context === null
                    ? t.noTarget
                    : target?.checking === true
                      ? t.checking
                      : t.pending
                }
              />
            ) : null}
            {state.context === null || target?.analysisBusy === true ? (
              <Reason
                id="analysis-read-blocked"
                entry={state.context === null ? t.noTarget : t.reading}
              />
            ) : null}
          </>
        }
      >
        <Button
          tier="secondary"
          label={t.analyze.short}
          onClick={() => {
            void store.analyze();
          }}
          {...(blocked ? { disabled: true, reasonId: 'analysis-blocked' } : {})}
        />
        <Button
          tier="quiet"
          label={t.readAnalysis.short}
          onClick={() => {
            void store.readAnalysis();
          }}
          {...(state.context === null || target?.analysisBusy === true
            ? { disabled: true, reasonId: 'analysis-read-blocked' }
            : {})}
        />
      </ActionRow>
      {target?.contextError === true ? <p role="status">{t.contextError.short}</p> : null}
      {answer.kind !== 'none' ? (
        <div role="status">
          <p>
            {answer.kind === 'accepted'
              ? t.accepted.short
              : answer.kind === 'refused'
                ? t.notStarted.short
                : answer.kind === 'sending'
                  ? t.sending.short
                  : t.unknown.short}
          </p>
          {answer.message !== null ? <Data value={answer.message} /> : null}
          {answer.kind === 'refused' ? (
            <>
              <Data value={answer.code} />
              <Data value={answer.error} />
            </>
          ) : null}
        </div>
      ) : null}
      <label className="tp-advanced__field">
        {t.operation.short}
        <input
          className="tp-field-control"
          value={target?.selectedOperation ?? ''}
          onChange={(e) => {
            store.selectOperation(e.target.value);
          }}
        />
      </label>
      {target?.analysisAt == null ? (
        <p>{t.noAnalysis.short}</p>
      ) : (
        <>
          {target.analysisError ? <p>{t.readError.short}</p> : null}
          {vm.analysisStale ? <p>{t.stale.short}</p> : null}
          <div role="status">
            {a.status === null ? t.unavailable.short : <Data value={a.status} />}
            {a.message === null ? null : <Data value={a.message} />}
            {a.code === null ? null : <Data value={a.code} />}
          </div>
          {a.available ? (
            <Facts layout="cells">
              <Fact label={t.screen.short} value={<Data value={a.screen} />} />
              <Fact label={t.candidates.short} value={<Values values={a.candidates} />} />
              <Fact
                label={t.tested.short}
                value={<Values values={[a.tested, a.total, a.completed]} />}
              />
              <Fact label={t.captured.short} value={<Data value={a.capturedAt} />} />
            </Facts>
          ) : null}
          {a.frame !== null && !target.snapshotError ? (
            <div className="tp-advanced__snapshot">
              <img
                key={a.frame}
                src={a.frame}
                alt={t.snapshot.short}
                onError={store.snapshotFailed}
              />
              {a.size !== null ? (
                <svg
                  aria-hidden="true"
                  viewBox={`0 0 ${a.size.width} ${a.size.height}`}
                  preserveAspectRatio="none"
                >
                  {a.matches.map((m) =>
                    m.rect === null ? null : (
                      <rect
                        key={m.id}
                        x={m.rect.x}
                        y={m.rect.y}
                        width={m.rect.w}
                        height={m.rect.h}
                      />
                    ),
                  )}
                </svg>
              ) : null}
            </div>
          ) : null}
          {target.snapshotError ? <p>{t.snapshotError.short}</p> : null}
          {a.noMatches ? <p>{t.noMatches.short}</p> : null}
          <ShownList
            items={a.matches}
            itemKey={(match) => match.id}
            more={t.more.short}
            render={(m) => (
              <>
                <Facts layout="cells">
                  <Fact label={t.names.short} value={<Values values={m.names} />} />
                  <Fact
                    label={t.roles.short}
                    value={m.roles.length === 0 ? t.unknownRole.short : <Values values={m.roles} />}
                  />
                  <Fact label={t.rect.short} value={<Coordinates value={m.rect} />} />
                  <Fact label={t.inliers.short} value={<Data value={m.inliers} />} />
                </Facts>
                {m.ambiguous ? <p>{t.ambiguous.short}</p> : null}
              </>
            )}
          />
        </>
      )}
    </>
  );
}
