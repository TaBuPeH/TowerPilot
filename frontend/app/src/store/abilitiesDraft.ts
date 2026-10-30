import { entries, list, own, ownPath, record, text } from '../core/read.ts';
import { viewEqual } from './store.ts';

/** Presentation drafts preserve numeric text and stable list identities independently
 * of parsed wire values. No ownership, cooldown or fleet decision is computed here. */
export type AbilityNode =
  | { readonly kind: 'value'; readonly value: string | boolean | null }
  | { readonly kind: 'number'; readonly raw: string }
  | { readonly kind: 'object'; readonly fields: Readonly<Record<string, AbilityNode>> }
  | {
      readonly kind: 'array';
      readonly items: readonly { readonly key: string; readonly node: AbilityNode }[];
    };
export type AbilityPath = readonly (string | number)[];
let serial = 0;
export function abilityNode(value: unknown): AbilityNode {
  if (typeof value === 'number') return { kind: 'number', raw: String(value) };
  if (typeof value === 'string' || typeof value === 'boolean' || value === null)
    return { kind: 'value', value };
  if (Array.isArray(value))
    return {
      kind: 'array',
      items: value.map((v: unknown) => ({ key: `ability-${++serial}`, node: abilityNode(v) })),
    };
  return {
    kind: 'object',
    fields: Object.fromEntries(entries(value).map(([k, v]) => [k, abilityNode(v)])),
  };
}
export function restoreAbilityNode(value: unknown): AbilityNode | null {
  const kind = own(value, 'kind');
  const raw = own(value, 'raw');
  if (kind === 'number' && typeof raw === 'string') return { kind, raw };
  const scalar = own(value, 'value');
  if (
    kind === 'value' &&
    (scalar === null || typeof scalar === 'string' || typeof scalar === 'boolean')
  )
    return { kind, value: scalar };
  if (kind === 'object' && record(own(value, 'fields')) !== undefined) {
    const fields: Record<string, AbilityNode> = {};
    for (const [key, item] of entries(own(value, 'fields'))) {
      const node = restoreAbilityNode(item);
      if (node === null) return null;
      Object.defineProperty(fields, key, {
        value: node,
        enumerable: true,
        writable: true,
        configurable: true,
      });
    }
    return { kind, fields };
  }
  if (kind === 'array' && list(own(value, 'items')) !== undefined) {
    const items: { key: string; node: AbilityNode }[] = [];
    for (const item of list(own(value, 'items')) ?? []) {
      const node = restoreAbilityNode(own(item, 'node'));
      const key = text(own(item, 'key'));
      if (node === null || key === undefined || items.some((i) => i.key === key)) return null;
      if (/^ability-\d+$/.test(key)) serial = Math.max(serial, Number(key.slice(8)));
      items.push({ key, node });
    }
    return { kind, items };
  }
  return null;
}
export function abilityValue(node: AbilityNode): unknown {
  if (node.kind === 'value') return node.value;
  if (node.kind === 'number') return node.raw.trim() === '' ? undefined : Number(node.raw);
  if (node.kind === 'array') return node.items.map((i) => abilityValue(i.node));
  return Object.fromEntries(
    Object.entries(node.fields).flatMap(([key, value]) => {
      const parsed = abilityValue(value);
      return parsed === undefined ? [] : [[key, parsed]];
    }),
  );
}
/** Refresh presentation fields independently. Rule identities follow exact saved rules,
 * including a reorder. A changed rule has no backend ID to associate safely: keep the
 * draft and expose the conflict instead of moving its hidden history to another rule. */
export function reconcileAbilityNode(
  node: AbilityNode,
  value: unknown,
  path: AbilityPath = [],
): AbilityNode | null {
  if (viewEqual(abilityValue(node), value)) return node;
  if (node.kind === 'object' && record(value) !== undefined) {
    if (Object.hasOwn(node.fields, 'rules') && own(value, 'rules') === undefined) return null;
    const fields: Record<string, AbilityNode> = {};
    for (const [key, next] of entries(value)) {
      const before = node.fields[key];
      const merged =
        before === undefined
          ? abilityNode(next)
          : reconcileAbilityNode(before, next, [...path, key]);
      if (merged === null) return null;
      Object.defineProperty(fields, key, {
        value: merged,
        enumerable: true,
        writable: true,
        configurable: true,
      });
    }
    return { kind: 'object', fields };
  }
  if (node.kind === 'array' && Array.isArray(value) && path.at(-1) === 'rules') {
    if (node.items.length !== value.length) return null;
    const remaining = [...node.items],
      items: typeof remaining = [];
    for (const next of value) {
      const index = remaining.findIndex((item) => viewEqual(abilityValue(item.node), next));
      if (index < 0) return null;
      const [matched] = remaining.splice(index, 1);
      if (matched !== undefined) items.push(matched);
    }
    return { kind: 'array', items };
  }
  if (node.kind === 'array' && Array.isArray(value)) {
    const items: { key: string; node: AbilityNode }[] = [];
    for (const [index, next] of value.entries()) {
      const before = node.items[index];
      if (before === undefined) {
        const added = abilityNode([next]);
        if (added.kind === 'array') items.push(...added.items);
      } else {
        const merged = reconcileAbilityNode(before.node, next, [...path, index]);
        if (merged === null) return null;
        items.push({ ...before, node: merged });
      }
    }
    return { kind: 'array', items };
  }
  return abilityNode(value);
}
/** Missing/null rules are a valid empty list, without changing the saved shape on read. */
export function abilityRules(node: AbilityNode): Extract<AbilityNode, { kind: 'array' }> | null {
  const rules = nodeAt(node, ['rules']);
  if (rules === undefined || (rules.kind === 'value' && rules.value === null))
    return { kind: 'array', items: [] };
  return rules.kind === 'array' ? rules : null;
}
export function nodeAt(node: AbilityNode, path: AbilityPath): AbilityNode | undefined {
  if (path.length === 0) return node;
  const [head, ...tail] = path;
  const next =
    node.kind === 'object' && typeof head === 'string'
      ? node.fields[head]
      : node.kind === 'array' && typeof head === 'number'
        ? node.items[head]?.node
        : undefined;
  return next === undefined ? undefined : nodeAt(next, tail);
}
export function setAbilityNode(
  node: AbilityNode,
  path: AbilityPath,
  value: AbilityNode | undefined,
): AbilityNode {
  if (path.length === 0) return value ?? node;
  const [head, ...tail] = path;
  if (node.kind === 'object' && typeof head === 'string') {
    const fields = { ...node.fields };
    if (tail.length === 0 && value === undefined) Reflect.deleteProperty(fields, head);
    else
      Object.defineProperty(fields, head, {
        value: setAbilityNode(node.fields[head] ?? abilityNode({}), tail, value),
        enumerable: true,
        writable: true,
        configurable: true,
      });
    return { ...node, fields };
  }
  if (node.kind === 'array' && typeof head === 'number')
    return {
      ...node,
      items: node.items.flatMap((item, index) =>
        index !== head
          ? [item]
          : tail.length === 0 && value === undefined
            ? []
            : [{ ...item, node: setAbilityNode(item.node, tail, value) }],
      ),
    };
  return node;
}
export function invalidAbilityNumbers(node: AbilityNode): boolean {
  if (node.kind === 'number')
    return (
      node.raw.trim() !== '' &&
      (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(node.raw.trim()) ||
        !Number.isFinite(Number(node.raw)))
    );
  if (node.kind === 'array')
    return node.items.some(
      (i) => invalidAbilityNumbers(i.node) || abilityValue(i.node) === undefined,
    );
  return node.kind === 'object' && Object.values(node.fields).some(invalidAbilityNumbers);
}
export const vocabValues = (vocab: unknown, key: string): readonly string[] =>
  (list(own(own(vocab, key), 'values')) ?? []).filter((v): v is string => typeof v === 'string');
export const specFields = (spec: unknown) => entries(own(spec, 'fields'));
export function specNode(spec: unknown): AbilityNode {
  const type = own(spec, 'type');
  if (type === 'object') return abilityNode({});
  if (type === 'list')
    return {
      kind: 'array',
      items: [
        { key: `ability-${++serial}`, node: { kind: 'number', raw: '' } },
        { key: `ability-${++serial}`, node: { kind: 'number', raw: '' } },
      ],
    };
  if (type === 'int' || type === 'float') return { kind: 'number', raw: '' };
  if (type === 'bool') return abilityNode(null);
  return abilityNode('');
}
export function ruleSupported(rule: unknown, vocab: unknown): boolean {
  if (
    record(rule) === undefined ||
    entries(rule).some(([key]) => !['when', 'do', 'repeat', 'refire_sec'].includes(key))
  )
    return false;
  const when = own(rule, 'when'),
    doValue = own(rule, 'do'),
    action = entries(doValue);
  if (
    (when !== undefined && record(when) === undefined) ||
    (doValue !== undefined && record(doValue) === undefined)
  )
    return false;
  const triggers = specFields(own(vocab, 'rule_triggers')).map(([key]) => key);
  const trigger = own(when, 'bar') !== undefined ? 'bar' : entries(when)[0]?.[0];
  return (
    (trigger === undefined
      ? entries(when).length === 0
      : triggers.includes(trigger) &&
        (trigger === 'bar'
          ? specShapeSupported(when, ownPath(vocab, 'rule_triggers', 'fields', trigger))
          : entries(when).length === 1 &&
            specShapeSupported(
              own(when, trigger),
              ownPath(vocab, 'rule_triggers', 'fields', trigger),
            ))) &&
    (action.length === 0 ||
      (action.length === 1 &&
        specFields(own(vocab, 'rule_actions')).some(
          ([key, spec]) => key === action[0]?.[0] && specShapeSupported(action[0][1], spec),
        ))) &&
    !['switch_cards', 'surrender_retry', 'stop_after_run'].includes(action[0]?.[0] ?? '')
  );
}
/** Structural editability only. The server still owns values, bounds and rule validity. */
function specShapeSupported(value: unknown, spec: unknown): boolean {
  if (own(spec, 'type') === 'list') return Array.isArray(value);
  if (own(spec, 'type') !== 'object') return record(value) === undefined && !Array.isArray(value);
  if (record(value) === undefined) return false;
  return entries(value).every(([key, child]) => {
    const childSpec = ownPath(spec, 'fields', key);
    return childSpec !== undefined && specShapeSupported(child, childSpec);
  });
}
