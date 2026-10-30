import { useId, useLayoutEffect, useState } from 'react';
import { copy } from '../../copy/catalogue.ts';
import { addDirective, moveDirective, removeDirective } from '../../core/shoppingLibrary.ts';
import type { QueueDraft } from '../../core/shoppingPlan.ts';
import type { ShoppingLibraryPolicyVM } from '../../store/selectors/shoppingLibrary.ts';
import { Button } from '../../ui/Button.tsx';
import { useAnnounce } from '../../ui/LiveRegion.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';

const t = copy.shoppingLibrary;

/** SQ10: legacy presentation and keyboard interactions; the core owner builds the saved value. */
export function LegacyEditor({
  policy,
  onDraft,
}: {
  readonly policy: ShoppingLibraryPolicyVM;
  readonly onDraft: (next: QueueDraft) => void;
}) {
  const model = policy.legacy;
  const own = useId();
  const announce = useAnnounce();
  const [focus, setFocus] = useState<{ readonly id: string; readonly serial: number } | null>(null);
  useLayoutEffect(() => {
    if (focus !== null) document.getElementById(focus.id)?.focus();
  }, [focus]);
  if (model === null) return null;
  const edit = (key: string, value: string | boolean) => {
    onDraft({ ...policy.draft, [key]: value });
  };
  const move = (key: string, step: -1 | 1) => {
    const next = moveDirective(policy.baseValue, policy.draft, key, step);
    if (next === null) return;
    onDraft(next);
    const position = model.lines.findIndex((line) => line.key === key) + step + 1;
    announce(t.moved(position));
    setFocus((previous) => ({ id: `${own}:${key}:${step}`, serial: (previous?.serial ?? 0) + 1 }));
  };
  return (
    <div className="tp-library__legacy">
      <p className="tp-library__warning">{t.legacyWarning.short}</p>
      {model.kind === 'list' ? (
        <p>{t.bare.short}</p>
      ) : (
        <div className="tp-library__fields">
          <label className="tp-library__check">
            <input
              type="checkbox"
              checked={model.enabled}
              onChange={(event) => {
                edit('enabled', event.currentTarget.checked);
              }}
            />
            <span>{t.enabled.short}</span>
          </label>
          <label>
            <span>{t.label.short}</span>
            <input
              className="tp-field-control"
              value={model.label}
              onChange={(event) => {
                edit('label', event.currentTarget.value);
              }}
            />
          </label>
        </div>
      )}
      {model.lines.map((line, index) => (
        <fieldset
          className="tp-library__directive"
          key={line.key}
          data-library-directive={line.key}
        >
          <legend>{t.line(index + 1)}</legend>
          <label className="tp-library__check">
            <input
              type="checkbox"
              checked={line.enabled}
              onChange={(event) => {
                edit(`${line.key}:enabled`, event.currentTarget.checked);
              }}
            />
            <span>{t.lineEnabled.short}</span>
          </label>
          <div className="tp-library__fields">
            <label>
              <span>{t.tab.short}</span>
              <select
                className="tp-field-control"
                value={line.tab}
                onChange={(event) => {
                  edit(`${line.key}:tab`, event.currentTarget.value);
                }}
              >
                <option value="">{t.choose.short}</option>
                {line.tabs.map((item) => (
                  <ServerOption key={item.value} value={item.value} label={item.label} />
                ))}
              </select>
            </label>
            <label>
              <span>{t.mode.short}</span>
              <select
                className="tp-field-control"
                value={line.mode}
                onChange={(event) => {
                  edit(`${line.key}:mode`, event.currentTarget.value);
                }}
              >
                <option value="">{t.choose.short}</option>
                {line.modes.map((item) => (
                  <ServerOption key={item.value} value={item.value} label={item.label} />
                ))}
              </select>
            </label>
            {line.mode !== 'clicks' ? null : (
              <label>
                <span id={`${own}:${line.key}:clicks-label`}>{t.clicks.short}</span>
                <input
                  className="tp-field-control"
                  type="text"
                  inputMode="numeric"
                  value={line.clicks}
                  aria-labelledby={`${own}:${line.key}:clicks-label`}
                  aria-invalid={line.clicksProblem !== null || undefined}
                  aria-describedby={`${own}:${line.key}:hint`}
                  onChange={(event) => {
                    edit(`${line.key}:clicks`, event.currentTarget.value);
                  }}
                />
                <span id={`${own}:${line.key}:hint`}>
                  {line.clicksProblem === 'empty'
                    ? t.clicksEmpty.short
                    : line.clicksProblem === 'not_a_number'
                      ? t.clicksNumber.short
                      : t.clicksHint.short}
                </span>
              </label>
            )}
          </div>
          <fieldset className="tp-library__stats">
            <legend>{t.stats.short}</legend>
            <p>{t.statsHint.short}</p>
            {line.statChoices.map((stat) => (
              <label className="tp-library__check" key={stat.value}>
                <input
                  type="checkbox"
                  checked={line.stats.includes(stat.value)}
                  onChange={(event) => {
                    edit(
                      `${line.key}:stats`,
                      JSON.stringify(
                        event.currentTarget.checked
                          ? [...line.stats, stat.value]
                          : line.stats.filter((value) => value !== stat.value),
                      ),
                    );
                  }}
                />
                <ServerValue kind="name" value={stat.label} />
              </label>
            ))}
          </fieldset>
          <div className="tp-library__actions tp-control-row">
            <span>
              <Button
                id={`${own}:${line.key}:-1`}
                tier="quiet"
                label={t.up.short}
                {...(line.first ? { disabled: true, reasonId: `${own}:${line.key}:first` } : {})}
                onClick={() => {
                  move(line.key, -1);
                }}
              />
              {line.first ? <span id={`${own}:${line.key}:first`}>{t.first.short}</span> : null}
            </span>
            <span>
              <Button
                id={`${own}:${line.key}:1`}
                tier="quiet"
                label={t.down.short}
                {...(line.last ? { disabled: true, reasonId: `${own}:${line.key}:last` } : {})}
                onClick={() => {
                  move(line.key, 1);
                }}
              />
              {line.last ? <span id={`${own}:${line.key}:last`}>{t.last.short}</span> : null}
            </span>
            <Button
              tier="quiet"
              label={t.removeLine.short}
              onClick={() => {
                onDraft(removeDirective(policy.baseValue, policy.draft, line.key));
                setFocus((previous) => ({ id: `${own}:add`, serial: (previous?.serial ?? 0) + 1 }));
              }}
            />
          </div>
        </fieldset>
      ))}
      <Button
        id={`${own}:add`}
        tier="secondary"
        label={t.add.short}
        onClick={() => {
          onDraft(addDirective(policy.baseValue, policy.draft));
        }}
      />
    </div>
  );
}
