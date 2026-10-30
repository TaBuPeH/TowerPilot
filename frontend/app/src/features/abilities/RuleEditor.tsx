import { useRef, useState } from 'react';
import { entries, own, ownPath } from '../../core/read.ts';
import {
  abilities as t,
  ruleNumber,
  ruleActionLabel,
  ruleMoved,
  abilityLabel,
} from '../../copy/abilities.ts';
import {
  abilityValue,
  nodeAt,
  ruleSupported,
  abilityRules,
  specFields,
  type AbilityNode,
} from '../../store/abilitiesDraft.ts';
import type { AbilitiesStore } from '../../store/abilities.ts';
import { Tile } from '../../ui/Tile.tsx';
import { ActionRow } from '../../ui/ActionRow.tsx';
import { AbilityField, Json } from './AbilityField.tsx';
import { AbilityHelp } from './AbilityHelp.tsx';
import { abilityFieldId, type AbilityProblem } from '../../store/selectors/abilities.ts';

export function RuleEditor({
  store,
  node,
  vocab,
  problems,
}: {
  readonly store: AbilitiesStore;
  readonly node: AbilityNode;
  readonly vocab: unknown;
  readonly problems: readonly AbilityProblem[];
}) {
  const [announcement, setAnnouncement] = useState(t.rules.short);
  const list = useRef<HTMLOListElement>(null);
  const rules = abilityRules(node);
  if (rules === null)
    return (
      <>
        <Json value={abilityValue(node)} />
        <p>{t.unsupported.short}</p>
      </>
    );
  const move = (action: 'up' | 'down' | 'delete', index: number, key: string) => {
    store.rule(action, index);
    setAnnouncement(ruleMoved(action === 'down' ? index + 1 : Math.max(0, index - 1)));
    requestAnimationFrame(() => {
      const cards = [...(list.current?.querySelectorAll<HTMLElement>('[data-rule-key]') ?? [])];
      const card =
        cards.find((c) => c.dataset['ruleKey'] === key) ?? cards[Math.min(index, cards.length - 1)];
      (
        card?.querySelector<HTMLElement>(`[data-rule-action="${action}"]:not(:disabled)`) ??
        card?.querySelector<HTMLElement>('select,button')
      )?.focus();
      if (cards.length === 0) document.getElementById('abilities-add-rule')?.focus();
    });
  };
  return (
    <Tile
      titleId="abilities-rules"
      title={t.rules.short}
      end={<AbilityHelp subject={t.rules.short} entries={[t.cooldownHelp, t.unavailable]} />}
    >
      <p role="status" className="tp-visually-hidden">
        {announcement}
      </p>
      <ol ref={list} className="tp-abilities__rules">
        {rules.items.map((item, index) => {
          const rule = abilityValue(item.node),
            action = own(rule, 'do');
          const whenNode = nodeAt(item.node, ['when']);
          const trigger =
            whenNode?.kind === 'object'
              ? Object.hasOwn(whenNode.fields, 'bar')
                ? 'bar'
                : (Object.keys(whenNode.fields)[0] ?? '')
              : '';
          const actionName = entries(action)[0]?.[0] ?? '';
          const supported = ruleSupported(rule, vocab);
          return (
            <li
              key={item.key}
              id={abilityFieldId(['rules', index])}
              tabIndex={-1}
              data-rule-key={item.key}
              className="tp-abilities__card tp-abilities__rule"
            >
              <h4>{ruleNumber(index)}</h4>
              {supported ? (
                <>
                  <label>
                    {t.when.short}
                    <select
                      className="tp-field-control"
                      id={abilityFieldId(['rules', index, 'condition'])}
                      aria-label={abilityLabel(`rule_${index + 1}_condition`)}
                      value={trigger}
                      onChange={(event) => {
                        store.rulePart(index, 'when', event.target.value);
                      }}
                    >
                      <option value="">{t.unspecified.short}</option>
                      {specFields(own(vocab, 'rule_triggers')).map(([key]) => (
                        <option key={key} value={key}>
                          {abilityLabel(key)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {trigger === '' ? null : (
                    <AbilityField
                      path={
                        trigger === 'bar'
                          ? ['rules', index, 'when']
                          : ['rules', index, 'when', trigger]
                      }
                      problems={problems}
                      label={abilityLabel(trigger)}
                      spec={ownPath(vocab, 'rule_triggers', 'fields', trigger)}
                      node={nodeAt(item.node, trigger === 'bar' ? ['when'] : ['when', trigger])}
                      onChange={(next) => {
                        store.edit(
                          trigger === 'bar'
                            ? ['rules', index, 'when']
                            : ['rules', index, 'when', trigger],
                          next,
                        );
                      }}
                    />
                  )}
                  <label>
                    {t.action.short}
                    <select
                      className="tp-field-control"
                      id={abilityFieldId(['rules', index, 'do'])}
                      aria-label={abilityLabel(`rule_${index + 1}_action`)}
                      value={actionName}
                      onChange={(event) => {
                        store.rulePart(index, 'do', event.target.value);
                      }}
                    >
                      <option value="">{t.unspecified.short}</option>
                      {specFields(own(vocab, 'rule_actions'))
                        .filter(([key]) => !['surrender_retry', 'switch_cards'].includes(key))
                        .map(([key]) => (
                          <option key={key} value={key}>
                            {abilityLabel(key)}
                          </option>
                        ))}
                    </select>
                  </label>
                  {actionName === '' ? null : (
                    <AbilityField
                      path={['rules', index, 'do', actionName]}
                      problems={problems}
                      label={abilityLabel(actionName)}
                      spec={ownPath(vocab, 'rule_actions', 'fields', actionName)}
                      node={nodeAt(item.node, ['do', actionName])}
                      onChange={(next) => {
                        store.edit(['rules', index, 'do', actionName], next);
                      }}
                    />
                  )}
                  <AbilityField
                    path={['rules', index, 'repeat']}
                    problems={problems}
                    label={t.repeat.short}
                    spec={{ type: 'bool' }}
                    node={nodeAt(item.node, ['repeat'])}
                    onChange={(next) => {
                      store.edit(['rules', index, 'repeat'], next);
                    }}
                  />
                  <AbilityField
                    path={['rules', index, 'refire_sec']}
                    problems={problems}
                    label={t.cooldown.short}
                    spec={{ type: 'float' }}
                    node={nodeAt(item.node, ['refire_sec'])}
                    onChange={(next) => {
                      store.edit(['rules', index, 'refire_sec'], next);
                    }}
                  />
                </>
              ) : (
                <>
                  <p>{t.unsupported.short}</p>
                  <Json value={rule} />
                </>
              )}
              <ActionRow>
                {(['up', 'down', 'delete'] as const).map((a) => (
                  <button
                    className="tp-button"
                    data-rule-action={a}
                    key={a}
                    type="button"
                    disabled={
                      a === 'up'
                        ? index === 0
                        : a === 'down'
                          ? index === rules.items.length - 1
                          : false
                    }
                    aria-label={ruleActionLabel(a, index)}
                    onClick={() => {
                      move(a, index, item.key);
                    }}
                  >
                    {a === 'up' ? t.up.short : a === 'down' ? t.down.short : t.remove.short}
                  </button>
                ))}
              </ActionRow>
            </li>
          );
        })}
      </ol>
      <ActionRow>
        <button
          type="button"
          className="tp-button"
          id="abilities-add-rule"
          onClick={() => {
            store.rule('add');
            setAnnouncement(t.add.short);
          }}
        >
          {t.add.short}
        </button>
      </ActionRow>
    </Tile>
  );
}
