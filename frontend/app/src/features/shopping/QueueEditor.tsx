import { useLayoutEffect, useState } from 'react';
import { copy } from '../../copy/catalogue.ts';
import type { CopyEntry } from '../../copy/text.ts';
import { entryKey, type EntryField, type FieldReview } from '../../core/shoppingPlan.ts';
import type {
  ChoiceVM,
  EntryVM,
  PickerSectionVM,
  QueueEditorVM,
} from '../../store/selectors/shoppingQueue.ts';
import { Button } from '../../ui/Button.tsx';
import { ActionRow, Reason } from '../../ui/ActionRow.tsx';
import { Facts, Fact } from '../../ui/Facts.tsx';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import { HelpTip } from '../../ui/HelpTip.tsx';
import { Tile } from '../../ui/Tile.tsx';
import './shopping.css';

const t = copy.shopping;

/** What the shared editor asks its owner to do with the draft (brief SQ3 to SQ6). */
export interface QueueEditorActions {
  readonly onEdit: (key: string, value: string | boolean) => void;
  readonly onInclude: (upgrade: string) => void;
  readonly onLeaveOut: (upgrade: string) => void;
  /** Move one place; the new 1-based position, or null when it cannot move. */
  readonly onMove: (upgrade: string, step: -1 | 1) => number | null;
  readonly onExclude: (upgrade: string, excluded: boolean) => void;
}

export interface QueueEditorProps extends QueueEditorActions {
  readonly vm: QueueEditorVM;
  /** Unique per editor on the page (saved run, live battle, library policy). */
  readonly idBase: string;
}

function Problem({ id, review }: { readonly id: string; readonly review: FieldReview }) {
  if (review.problem === null) return null;
  return (
    <span id={id} className="tp-shop__problem">
      {t.problems[review.problem].short}
    </span>
  );
}

/** Background for a numeric field: its own explanation, then its hint's. Nothing without either. */
function FieldHelp({ label, hint }: { readonly label: CopyEntry; readonly hint: CopyEntry }) {
  if (label.why === undefined && hint.why === undefined) return null;
  return (
    <HelpTip label={label.short}>
      {label.why === undefined ? null : <p>{label.why}</p>}
      {hint.why === undefined ? null : <p>{hint.why}</p>}
    </HelpTip>
  );
}

function NumberInput(props: {
  readonly id: string;
  readonly label: CopyEntry;
  readonly hint: CopyEntry;
  /** Shared guidance shown once above the queue, rather than under every entry. */
  readonly hintId?: string;
  readonly review: FieldReview;
  readonly onChange: (value: string) => void;
}) {
  const { id, label, hint, hintId, review, onChange } = props;
  const invalid = review.problem !== null;
  const descriptionId = hintId ?? `${id}-hint`;
  return (
    <div className="tp-shop__field">
      <span className="tp-shop__label">
        <label htmlFor={id}>{label.short}</label>
        {hintId === undefined ? <FieldHelp label={label} hint={hint} /> : null}
      </span>
      <input
        id={id}
        className="tp-field-control"
        type="text"
        inputMode="decimal"
        value={review.raw}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? `${id}-problem ${descriptionId}` : descriptionId}
        onChange={(event) => {
          onChange(event.currentTarget.value);
        }}
      />
      {hintId === undefined ? (
        <span id={descriptionId} className="tp-shop__hint">
          {hint.short}
        </span>
      ) : null}
      <Problem id={`${id}-problem`} review={review} />
    </div>
  );
}

function Choice<K extends string>(props: {
  readonly id: string;
  readonly label: CopyEntry;
  readonly value: string;
  readonly problem: FieldReview['problem'];
  readonly choices: readonly ChoiceVM<K>[];
  readonly words: Readonly<Record<K, CopyEntry>>;
  readonly onChange: (value: string) => void;
}) {
  const { id, label, value, problem, choices, words, onChange } = props;
  const chosen = choices.find((choice) => choice.value === value);
  return (
    <div className="tp-shop__field">
      <span className="tp-shop__label">
        <label htmlFor={id}>{label.short}</label>
        <HelpTip label={label.short}>
          {choices.map((choice) => {
            if (choice.known === null) return null;
            const { short, why } = words[choice.known];
            return (
              <p key={choice.value}>
                <strong>{short}</strong>
                {why === undefined ? null : (
                  <>
                    {t.space}
                    {why}
                  </>
                )}
              </p>
            );
          })}
        </HelpTip>
      </span>
      <select
        id={id}
        className="tp-field-control"
        value={value}
        aria-invalid={problem !== null || undefined}
        aria-describedby={problem === null ? undefined : `${id}-problem`}
        onChange={(event) => {
          onChange(event.currentTarget.value);
        }}
      >
        {value === '' ? <option value="">{t.choose.short}</option> : null}
        {choices.map((choice) =>
          choice.known !== null ? (
            <option key={choice.value} value={choice.value}>
              {words[choice.known].short}
            </option>
          ) : choice.server === null ? null : (
            <ServerOption key={choice.value} value={choice.value} label={choice.server} />
          ),
        )}
      </select>
      {problem === null ? null : (
        <span id={`${id}-problem`} className="tp-shop__problem">
          {t.problems[problem].short}
        </span>
      )}
      {chosen !== undefined && chosen.known === null ? (
        <span className="tp-shop__hint">{t.unknownChoice.short}</span>
      ) : null}
    </div>
  );
}

const FIELDS: readonly {
  readonly field: EntryField;
  readonly label: CopyEntry;
  readonly hint: CopyEntry;
}[] = [
  { field: 'tap_limit', label: t.tapLimit, hint: t.tapLimitHint },
  { field: 'weight', label: t.weight, hint: t.weightHint },
  { field: 'from_wave', label: t.fromWave, hint: t.fromWaveHint },
  { field: 'until_wave', label: t.untilWave, hint: t.untilWaveHint },
];

function Entry(props: {
  readonly entry: EntryVM;
  readonly idBase: string;
  readonly actions: QueueEditorActions;
  readonly moved: (upgrade: string, step: -1 | 1) => void;
}) {
  const { entry, idBase, actions, moved } = props;
  const base = `${idBase}:entry:${entry.id}`;
  return (
    <li className="tp-shop__entry" data-upgrade={entry.id}>
      <Tile
        as="fieldset"
        titleId={`${base}:name`}
        title={
          <>
            {t.position(entry.position)}
            {t.space}
            {entry.label === null ? null : <ServerValue kind="name" value={entry.label} />}
          </>
        }
        density="compact"
      >
        <label className="tp-shop__check">
          <input
            id={`${base}:enabled`}
            type="checkbox"
            checked={entry.enabled}
            onChange={(event) => {
              if (event.currentTarget.checked) actions.onInclude(entry.id);
              else actions.onLeaveOut(entry.id);
            }}
          />
          <span>{t.included.short}</span>
        </label>
        {entry.added ? <span className="tp-shop__hint">{t.added.short}</span> : null}
        <div className="tp-shop__fields">
          {FIELDS.map(({ field, label, hint }) => (
            <NumberInput
              key={field}
              id={`${base}:${field}`}
              label={label}
              hint={hint}
              hintId={`${idBase}:hint:${field}`}
              review={entry.fields[field]}
              onChange={(value) => {
                actions.onEdit(entryKey(entry.id, field), value);
              }}
            />
          ))}
        </div>
        <div className="tp-shop__moves">
          <ActionRow
            reason={
              entry.first || entry.last ? (
                <>
                  {entry.first ? <Reason id={`${base}:first`} entry={t.firstPlace} /> : null}
                  {entry.last ? <Reason id={`${base}:last`} entry={t.lastPlace} /> : null}
                </>
              ) : null
            }
          >
            <Button
              id={`${base}:up`}
              tier="quiet"
              label={t.up.short}
              targetId={`${base}:name`}
              {...(entry.first ? { disabled: true, reasonId: `${base}:first` } : {})}
              onClick={() => {
                moved(entry.id, -1);
              }}
            />
            <Button
              id={`${base}:down`}
              tier="quiet"
              label={t.down.short}
              targetId={`${base}:name`}
              {...(entry.last ? { disabled: true, reasonId: `${base}:last` } : {})}
              onClick={() => {
                moved(entry.id, 1);
              }}
            />
          </ActionRow>
        </div>
      </Tile>
    </li>
  );
}

function Picker(props: {
  readonly section: PickerSectionVM;
  readonly idBase: string;
  readonly actions: QueueEditorActions;
}) {
  const { section, idBase, actions } = props;
  return (
    <Tile
      as="fieldset"
      className="tp-shop__page"
      titleId={`${idBase}:section:${section.key}`}
      title={
        section.known !== null ? (
          t.sections[section.known].short
        ) : section.server === null ? (
          t.otherSection.short
        ) : (
          <ServerValue kind="name" value={section.server} />
        )
      }
      density="compact"
    >
      <div
        className="tp-shop__page-viewport"
        role="region"
        aria-labelledby={`${idBase}:section:${section.key}`}
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The bounded upgrade list must support keyboard scrolling.
        tabIndex={0}
      >
        <ul>
          {section.items.map((item) => {
            const base = `${idBase}:pick:${item.id}`;
            return (
              <li key={item.id} className="tp-shop__pick" data-upgrade={item.id}>
                <label className="tp-shop__check">
                  <input
                    id={`${base}:included`}
                    type="checkbox"
                    checked={item.included}
                    aria-describedby={item.excluded ? `${base}:reason` : undefined}
                    onChange={(event) => {
                      if (event.currentTarget.checked) actions.onInclude(item.id);
                      else actions.onLeaveOut(item.id);
                    }}
                  />
                  {item.label === null ? null : <ServerValue kind="name" value={item.label} />}
                </label>
                <label className="tp-shop__check tp-shop__exclude">
                  <input
                    id={`${base}:excluded`}
                    type="checkbox"
                    checked={item.excluded}
                    onChange={(event) => {
                      actions.onExclude(item.id, event.currentTarget.checked);
                    }}
                  />
                  <span>{t.excluded.short}</span>
                </label>
                {item.excluded && item.exclusionReason !== null ? (
                  <span id={`${base}:reason`} className="tp-shop__hint">
                    {t.excludedReason.short}
                    {t.space}
                    <ServerValue kind="message" value={item.exclusionReason} />
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </Tile>
  );
}

/**
 * The one shopping queue editor (brief SQ3 to SQ6): purchase rules, the queue in priority order
 * with named Up/Down controls, and the shop pages to include upgrades from. It renders a draft
 * and reports edits; its owner keeps the draft, the target and the Save row.
 */
export function QueueEditor(props: QueueEditorProps) {
  const { vm, idBase } = props;
  const announce = useAnnounce();
  const [focusTarget, setFocusTarget] = useState<{
    readonly id: string;
    readonly serial: number;
  } | null>(null);
  useLayoutEffect(() => {
    if (focusTarget !== null) document.getElementById(focusTarget.id)?.focus();
  }, [focusTarget]);
  const moved = (upgrade: string, step: -1 | 1) => {
    const position = props.onMove(upgrade, step);
    if (position === null) return;
    announce(t.moved(position));
    // Focus stays on the moved entry's control (the list re-orders around it).
    setFocusTarget((previous) => ({
      id: `${idBase}:entry:${upgrade}:${step < 0 ? 'up' : 'down'}`,
      serial: (previous?.serial ?? 0) + 1,
    }));
  };
  const review = vm.review;
  return (
    <div className="tp-shop">
      <Tile
        as="fieldset"
        className="tp-shop__rules"
        titleId={`${idBase}:rules`}
        title={t.rules.short}
      >
        <NumberInput
          id={`${idBase}:reserve`}
          label={t.reserve}
          hint={t.reserveHint}
          review={review.reserve}
          onChange={(value) => {
            props.onEdit('reserve', value);
          }}
        />
        <Choice
          id={`${idBase}:mode`}
          label={t.mode}
          value={vm.mode.value}
          problem={review.modeProblem}
          choices={vm.mode.choices}
          words={t.modes}
          onChange={(value) => {
            props.onEdit('mode', value);
          }}
        />
        <Choice
          id={`${idBase}:cadence`}
          label={t.cadence}
          value={vm.cadence.value}
          problem={review.cadenceProblem}
          choices={vm.cadence.choices}
          words={t.cadences}
          onChange={(value) => {
            props.onEdit('cadence', value);
          }}
        />
      </Tile>
      <div className="tp-shop__columns">
        <Tile
          className="tp-shop__queue"
          titleId={`${idBase}:queue`}
          title={t.queue.short}
          level={4}
          intro={t.queueHint}
        >
          {vm.entries.length === 0 ? (
            <p>{t.emptyQueue.short}</p>
          ) : (
            <>
              <Facts layout="cells" className="tp-shop__guidance">
                {FIELDS.map(({ field, label, hint }) => (
                  <Fact
                    key={field}
                    id={`${idBase}:hint:${field}`}
                    label={
                      <>
                        {label.short}
                        <FieldHelp label={label} hint={hint} />
                      </>
                    }
                    value={hint.short}
                  />
                ))}
              </Facts>
              <ol>
                {vm.entries.map((entry) => (
                  <Entry
                    key={entry.id}
                    entry={entry}
                    idBase={idBase}
                    actions={props}
                    moved={moved}
                  />
                ))}
              </ol>
            </>
          )}
        </Tile>
        <Tile
          className="tp-shop__picker"
          titleId={`${idBase}:picker`}
          title={t.picker.short}
          level={4}
          intro={t.pickerHint}
        >
          {vm.orderProvisional ? <p className="tp-shop__hint">{t.orderProvisional.short}</p> : null}
          {vm.picker.map((section) => (
            <Picker key={section.key} section={section} idBase={idBase} actions={props} />
          ))}
        </Tile>
      </div>
      {review.problems === 0 ? null : (
        <p className="tp-shop__problem">{t.problemCount(review.problems)}</p>
      )}
    </div>
  );
}
