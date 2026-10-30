// @ts-check
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import js from '@eslint/js';
import { createTypeScriptImportResolver } from 'eslint-import-resolver-typescript';
import { createNodeResolver, importX } from 'eslint-plugin-import-x';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import { defineConfig, globalIgnores } from 'eslint/config';
import globals from 'globals';
import { configs as tsConfigs } from 'typescript-eslint';
import towerPilot from './eslint-rules/index.js';
import {
  BRAND_HOMES,
  coreGlobals,
  coreImports,
  coreSyntax,
  domTextSinks,
  htmlStringSyntax,
  layerZones,
  copyRestrictedImports,
  networkOutsideApi,
  selectorRestrictedImports,
  srcRestrictedImports,
  storeTypeOnlyApi,
} from './eslint-rules/policy.js';
import reactPackage from 'react/package.json' with { type: 'json' };

const HERE = path.dirname(fileURLToPath(import.meta.url));

// eslint-plugin-react 7.37.5 declares ESLint ^9 and runs on ESLint 10 through
// the package.json override. Its `version: 'detect'` calls context.getFilename,
// which ESLint 10 removed, so the installed React version is read here instead.
// Its rules forward-ref-uses-ref and jsx-filename-extension also call removed
// APIs directly and must stay off. Drop this when the plugin supports ESLint 10.
const REACT_VERSION = reactPackage.version;

// The policy itself (layers, network, brands, copy) lives in eslint-rules/policy.js,
// shared with tests/unit/lint/rules.test.ts.
export default defineConfig([
  // The generated contract modules are checked by `generate-contract.mjs --check`, not linted.
  globalIgnores([
    'node_modules/',
    'test-results/',
    'playwright-report/',
    'coverage/',
    'src/contract/generated/',
  ]),

  // ------------------------------------------------------------ everywhere
  js.configs.recommended,
  tsConfigs.strictTypeChecked,
  importX.flatConfigs.recommended,
  importX.flatConfigs.typescript,
  {
    plugins: { 'tower-pilot': towerPilot },
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.json', './tsconfig.test.json', './tsconfig.node.json'],
        tsconfigRootDir: HERE,
      },
    },
    settings: {
      'import-x/resolver-next': [
        createTypeScriptImportResolver({
          project: ['./tsconfig.json', './tsconfig.test.json', './tsconfig.node.json'],
          noWarnOnMultipleProjects: true,
        }),
        createNodeResolver(),
      ],
    },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules: {
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      'import-x/no-cycle': 'error',
      // Relative imports name their file extension, as Vite's native config loader and Node need.
      'import-x/extensions': ['error', 'ignorePackages'],
      'import-x/no-restricted-paths': ['error', { basePath: HERE, zones: layerZones }],
    },
  },

  // ---------------------------------------------- application source (browser)
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    rules: {
      'import-x/no-nodejs-modules': 'error',
      // No type assertions in application code (ADR-001 rule 2): an assertion is how a
      // value skips a check. `as const` stays allowed; the brand homes are exempt below.
      '@typescript-eslint/consistent-type-assertions': ['error', { assertionStyle: 'never' }],
      'no-restricted-globals': ['error', networkOutsideApi],
      'no-restricted-properties': ['error', ...domTextSinks],
      // core/ sets this rule again below with the same selectors: the later entry replaces this one.
      'no-restricted-syntax': ['error', ...htmlStringSyntax],
      'no-restricted-imports': ['error', srcRestrictedImports],
      'no-alert': 'error',
      'no-eval': 'error',
      'no-new-func': 'error',
    },
  },
  {
    files: ['src/**/*.tsx'],
    ...react.configs.flat['recommended'],
    settings: { react: { version: REACT_VERSION } },
  },
  {
    files: ['src/**/*.tsx'],
    ...react.configs.flat['jsx-runtime'],
  },
  {
    files: ['src/**/*.tsx'],
    ...jsxA11y.flatConfigs.strict,
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ...reactHooks.configs.flat['recommended-latest'],
  },
  {
    files: ['src/**/*.tsx'],
    plugins: { react },
    rules: {
      // ADR-001 rule 5: no wording in components. JSX text is caught here, a plain
      // string-typed child by tower-pilot/no-string-jsx-child.
      'react/jsx-no-literals': ['error', { noStrings: true, ignoreProps: true }],
      'tower-pilot/no-string-jsx-child': 'error',
      'react/no-danger': 'error',
      'react-hooks/exhaustive-deps': 'error',
      // Props are typed by TypeScript.
      'react/prop-types': 'off',
    },
  },
  // The one component that renders server text, inside its marked <bdi> (dashboard-spec 6.3).
  {
    files: ['src/ui/ServerValue.tsx'],
    rules: {
      'tower-pilot/no-string-jsx-child': ['error', { allowServerText: true }],
    },
  },
  // Store selectors make server text; store/ reads api/ types (type-only imports).
  {
    files: ['src/store/**/*.ts'],
    rules: {
      '@typescript-eslint/no-restricted-imports': ['error', storeTypeOnlyApi],
    },
  },
  {
    files: ['src/store/selectors/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', selectorRestrictedImports],
    },
  },
  {
    files: ['src/copy/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', copyRestrictedImports],
    },
  },
  // The network may be used only from src/api/.
  {
    files: ['src/api/**/*.ts'],
    rules: {
      'no-restricted-globals': 'off',
    },
  },
  // core/: no React, no DOM, no network, no JSX.
  {
    files: ['src/core/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', coreImports],
      'no-restricted-globals': ['error', coreGlobals],
      'no-restricted-syntax': ['error', ...coreSyntax],
    },
  },
  // The homes of the type-only brands make (assert) their own brand.
  {
    files: BRAND_HOMES,
    rules: {
      '@typescript-eslint/consistent-type-assertions': 'off',
    },
  },

  // ------------------------------------------------------------ tests
  {
    files: ['tests/unit/**/*.{ts,tsx}', 'tests/support/**/*.{ts,tsx}'],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
  },

  // --------------------------------------------- tooling (Node, not shipped)
  {
    files: [
      '*.{js,ts}',
      'scripts/**/*.{ts,mjs}',
      'tests/e2e/**/*.{ts,mjs}',
      'tests/support/**/*.{ts,mjs}',
      'eslint-rules/**/*.js',
    ],
    languageOptions: { globals: globals.node },
  },
]);
