// @ts-check
/** The Tower Pilot lint rule (ADR-001 rule 5); type-aware, configured in eslint.config.js. */
import noStringJsxChild from './no-string-jsx-child.js';

// typescript-eslint's RuleModule and ESLint's Plugin types describe the same
// runtime object but do not line up; ESLint accepts the rule as it is.
export default /** @type {import('eslint').ESLint.Plugin} */ (
  /** @type {unknown} */ ({
    meta: { name: 'tower-pilot' },
    rules: { 'no-string-jsx-child': noStringJsxChild },
  })
);
