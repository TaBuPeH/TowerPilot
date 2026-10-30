import { useId } from 'react';
import { entries, list, own } from '../../core/read.ts';
import { abilities as t, abilityLabel } from '../../copy/abilities.ts';
import {
  abilityNode,
  abilityValue,
  invalidAbilityNumbers,
  nodeAt,
  specFields,
  specNode,
  type AbilityNode,
  type AbilityPath,
} from '../../store/abilitiesDraft.ts';
import {
  abilityMessage,
  abilityName,
  abilityJson,
  abilityFieldId,
  abilityFieldProblems,
  type AbilityProblem,
} from '../../store/selectors/abilities.ts';
import { AbilityHelp } from './AbilityHelp.tsx';
import { ServerOption, ServerValue } from '../../ui/ServerValue.tsx';
import type { CopyText } from '../../copy/text.ts';

export function AbilityField({
  label,
  spec,
  node,
  onChange,
  required = false,
  path,
  problems = [],
  onOptional,
}: {
  readonly label: CopyText;
  readonly spec: unknown;
  readonly node: AbilityNode | undefined;
  readonly onChange: (node: AbilityNode) => void;
  readonly required?: boolean;
  readonly path?: AbilityPath | undefined;
  readonly problems?: readonly AbilityProblem[];
  readonly onOptional?: ((enabled: boolean) => void) | undefined;
}) {
  const fallbackId = useId(),
    id = path === undefined ? fallbackId : abilityFieldId(path),
    type = own(spec, 'type'),
    help = abilityMessage(own(spec, 'doc'));
  const current = node ?? specNode(spec),
    value = abilityValue(current);
  if (type === 'object') {
    const fields = specFields(spec),
      requiredFields = list(own(spec, 'required')) ?? [];
    const extra = entries(value).filter(([key]) => !fields.some(([name]) => name === key));
    return (
      <fieldset className="tp-abilities__field-group" id={id} tabIndex={-1}>
        <legend>{label}</legend>
        {help === null ? null : (
          <p>
            <ServerValue kind="message" value={help} />
          </p>
        )}
        {fields.map(([key, child]) => (
          <AbilityField
            key={key}
            label={abilityLabel(key)}
            spec={child}
            node={nodeAt(current, [key])}
            required={requiredFields.includes(key)}
            path={path === undefined ? undefined : [...path, key]}
            problems={problems}
            onChange={(next) => {
              const fields = current.kind === 'object' ? current.fields : {};
              onChange({ kind: 'object', fields: { ...fields, [key]: next } });
            }}
          />
        ))}
        {extra.length === 0 ? null : (
          <details>
            <summary>{t.unknownFields.short}</summary>
            <Json value={Object.fromEntries(extra)} />
          </details>
        )}
      </fieldset>
    );
  }
  if (type === 'list') {
    const pair = current.kind === 'array' ? current : specNode({ type: 'list' });
    const enabled = onOptional === undefined || (node !== undefined && value !== null);
    return (
      <fieldset className="tp-abilities__field-group" id={id} tabIndex={-1}>
        <legend>{label}</legend>
        {onOptional === undefined ? null : (
          <>
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => {
                  onOptional(event.target.checked);
                }}
              />
              {t.explicitBounds.short}
            </label>
            <AbilityHelp subject={t.explicitBounds.short} entries={[t.optionalBounds]} />
          </>
        )}
        {help === null ? null : (
          <p>
            <ServerValue kind="message" value={help} />
          </p>
        )}
        <fieldset className="tp-abilities__pair" disabled={!enabled}>
          <legend className="tp-visually-hidden">{t.bounds.short}</legend>
          {[0, 1].map((index) => (
            <AbilityField
              key={index}
              label={index === 0 ? t.minimum.short : t.maximum.short}
              spec={{ type: 'int' }}
              node={nodeAt(pair, [index])}
              required={enabled}
              path={path === undefined ? undefined : [...path, index]}
              problems={problems}
              onChange={(next) => {
                if (pair.kind === 'array')
                  onChange({
                    ...pair,
                    items: pair.items.map((item, i) =>
                      i === index ? { ...item, node: next } : item,
                    ),
                  });
              }}
            />
          ))}
        </fieldset>
        {current.kind === 'array' && current.items.length !== 2 ? <Json value={value} /> : null}
      </fieldset>
    );
  }
  const numeric = type === 'int' || type === 'float';
  const errors = path === undefined ? [] : abilityFieldProblems(problems, path);
  const numericInvalid = numeric && invalidAbilityNumbers(current);
  const invalid = numericInvalid || errors.length > 0;
  const options = (list(own(spec, 'values')) ?? []).filter(
    (v): v is string => typeof v === 'string',
  );
  return (
    <div className="tp-abilities__field">
      <label htmlFor={id}>
        {label}
        {required ? <span className="tp-abilities__required">{t.required.short}</span> : null}
      </label>
      {type === 'bool' ? (
        <select
          className="tp-field-control"
          id={id}
          aria-invalid={invalid}
          aria-describedby={`${id}-help`}
          value={typeof value === 'boolean' ? String(value) : ''}
          onChange={(event) => {
            onChange(abilityNode(event.target.value === '' ? null : event.target.value === 'true'));
          }}
        >
          <option value="">{t.unspecified.short}</option>
          <option value="true">{t.on.short}</option>
          <option value="false">{t.off.short}</option>
        </select>
      ) : type === 'enum' ? (
        <select
          className="tp-field-control"
          id={id}
          aria-invalid={invalid}
          aria-describedby={`${id}-help`}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => {
            onChange(abilityNode(event.target.value));
          }}
        >
          <option value="">{t.unspecified.short}</option>
          {options.map((option) => {
            const name = abilityName(option);
            return name === null ? null : <ServerOption key={option} value={option} label={name} />;
          })}
          {typeof value === 'string' && value !== '' && !options.includes(value) ? (
            <option value={value}>{t.unsupported.short}</option>
          ) : null}
        </select>
      ) : (
        <input
          className="tp-field-control"
          id={id}
          type="text"
          inputMode={numeric ? 'decimal' : undefined}
          value={current.kind === 'number' ? current.raw : typeof value === 'string' ? value : ''}
          aria-invalid={invalid}
          aria-describedby={`${id}-help`}
          onChange={(event) => {
            onChange(
              numeric
                ? { kind: 'number', raw: event.target.value }
                : abilityNode(event.target.value),
            );
          }}
        />
      )}
      <div id={`${id}-help`}>
        {help === null ? null : (
          <p>
            <ServerValue kind="message" value={help} />
          </p>
        )}
        {numericInvalid ? <p role="alert">{t.numericError.short}</p> : null}
        {errors.map((error, index) => (
          <p key={index}>
            <ServerValue kind="message" value={error.message} />
          </p>
        ))}
      </div>
    </div>
  );
}
export function Json({ value }: { readonly value: unknown }) {
  const shown = abilityJson(value);
  return shown === null ? null : (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Scrollable policy data needs keyboard focus (WCAG 2.1.1).
    <pre tabIndex={0} role="region" aria-label={t.savedDetails.short}>
      <ServerValue kind="detail" value={shown} />
    </pre>
  );
}
export function AbilityBackendErrors({
  problems,
  path,
}: {
  readonly problems: readonly AbilityProblem[];
  readonly path: AbilityPath;
}) {
  return (
    <div id={`${abilityFieldId(path)}-server-errors`}>
      {abilityFieldProblems(problems, path).map((error, index) => (
        <p key={index}>
          <ServerValue kind="message" value={error.message} />
        </p>
      ))}
    </div>
  );
}
