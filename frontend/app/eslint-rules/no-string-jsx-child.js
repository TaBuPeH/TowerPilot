// @ts-check
/**
 * What React renders comes from the copy catalogue (CopyText) or the server
 * (ServerText, rendered only by ui/ServerValue.tsx inside its marked <bdi>),
 * both branded strings (ADR-001 rule 5, dashboard-spec 6.3). This rule refuses
 * a JSX child whose type is, or may hold, a plain string (directly, or inside
 * an array, tuple, iterable or promise, all of which React renders), and a
 * ServerText child anywhere except where `allowServerText` is set (the one
 * ServerValue component). String literals are react/jsx-no-literals' job; this
 * rule catches the variable.
 */
import { AST_NODE_TYPES, ESLintUtils } from '@typescript-eslint/utils';
import * as ts from 'typescript';

const createRule = ESLintUtils.RuleCreator((name) => `frontend/app/eslint-rules/${name}.js`);

/** ServerText's brand: the unique symbol `serverBrand` of src/copy/server.ts. */
const SERVER_BRAND = /^__@serverBrand@/u;

/**
 * @param {ts.TypeChecker} checker
 * @param {ts.Type} type
 */
function isServerText(checker, type) {
  return (
    type.isIntersection() &&
    checker
      .getPropertiesOfType(type)
      .some((property) => SERVER_BRAND.test(String(property.escapedName)))
  );
}

/**
 * The types an iterable object yields (Set<string>, Iterable<string>, or any
 * object with a [Symbol.iterator]() method): the `value` of each result its
 * iterator's `next()` returns. A non-iterable type yields nothing.
 * @param {ts.TypeChecker} checker
 * @param {ts.Type} type
 * @returns {readonly ts.Type[]}
 */
function iterableMembers(checker, type) {
  if ((type.flags & ts.TypeFlags.Object) === 0) return [];
  const iterator = checker
    .getPropertiesOfType(type)
    .find((property) => String(property.escapedName).startsWith('__@iterator@'));
  if (iterator === undefined) return [];
  /** @type {ts.Type[]} */
  const yielded = [];
  for (const signature of checker.getTypeOfSymbol(iterator).getCallSignatures()) {
    const next = signature.getReturnType().getProperty('next');
    if (next === undefined) continue;
    for (const nextSignature of checker.getTypeOfSymbol(next).getCallSignatures()) {
      const result = nextSignature.getReturnType();
      for (const member of result.isUnion() ? result.types : [result]) {
        const value = member.getProperty('value');
        if (value !== undefined) yielded.push(checker.getTypeOfSymbol(value));
      }
    }
  }
  return yielded;
}

/**
 * 'plain' when the type is or may hold a plain string, 'server' when it is or
 * may hold ServerText, null otherwise.
 * @param {ts.TypeChecker} checker
 * @param {ts.Type} type
 * @param {Set<ts.Type>} seen
 * @returns {'plain' | 'server' | null}
 */
function classify(checker, type, seen = new Set()) {
  if (seen.has(type)) return null;
  seen.add(type);
  if (type.isUnion()) {
    for (const member of type.types) {
      const found = classify(checker, member, seen);
      if (found !== null) return found;
    }
    return null;
  }
  if (isServerText(checker, type)) return 'server';
  // Any other intersection with a string is a branded string (CopyText).
  if (type.isIntersection()) return null;
  if ((type.flags & ts.TypeFlags.StringLike) !== 0) return 'plain';
  const awaited = checker.getAwaitedType(type);
  /** @type {readonly ts.Type[]} */
  const members =
    checker.isArrayType(type) || checker.isTupleType(type)
      ? checker.getTypeArguments(/** @type {ts.TypeReference} */ (type))
      : awaited !== undefined && awaited !== type
        ? [awaited]
        : iterableMembers(checker, type);
  for (const member of members) {
    const found = classify(checker, member, seen);
    if (found !== null) return found;
  }
  return null;
}

export default createRule({
  name: 'no-string-jsx-child',
  meta: {
    type: 'problem',
    docs: {
      description:
        'Refuse a plain string-typed JSX child, and ServerText outside ServerValue (ADR-001 rule 5).',
    },
    messages: {
      plain:
        'A JSX child must be catalogue text (CopyText) or server text in ServerValue, not a plain string (ADR-001 rule 5).',
      server:
        'Server text renders only through ServerValue (ui/ServerValue.tsx), inside its marked <bdi> (dashboard-spec 6.3).',
    },
    schema: [
      {
        type: 'object',
        properties: { allowServerText: { type: 'boolean' } },
        additionalProperties: false,
      },
    ],
    defaultOptions: [{ allowServerText: false }],
  },
  create(context, [options]) {
    const services = ESLintUtils.getParserServices(context);
    const checker = services.program.getTypeChecker();
    return {
      JSXExpressionContainer(node) {
        const parent = node.parent.type;
        if (parent !== AST_NODE_TYPES.JSXElement && parent !== AST_NODE_TYPES.JSXFragment) return;
        if (node.expression.type === AST_NODE_TYPES.JSXEmptyExpression) return;
        const found = classify(checker, services.getTypeAtLocation(node.expression));
        if (found === 'plain') context.report({ node, messageId: 'plain' });
        if (found === 'server' && options?.allowServerText !== true) {
          context.report({ node, messageId: 'server' });
        }
      },
    };
  },
});
