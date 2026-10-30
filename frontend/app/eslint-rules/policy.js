// @ts-check
/**
 * The lint policy of ADR-001 after the F1 trim (council A-04): layer zones, no
 * wording in components, no type assertions outside the brand homes, network
 * only from src/api/, and a few dangerous builtins. Guardrails against honest
 * mistakes, not a security boundary (ADR Amendment 1). eslint.config.js
 * applies these entries; tests/unit/lint/rules.test.ts lints probes with the same
 * entries, so the test and the config cannot drift apart.
 */

// ---------------------------------------------------------------- layers
/**
 * Each layer lists the OTHER layers it may import; everything else is refused
 * by import-x/no-restricted-paths. store/ may import api/ for types only
 * (`storeTypeOnlyApi` below): app/ starts the poller and the write machine and
 * hands their results to store/; components everywhere read store/ view models.
 * @type {Record<string, string[]>}
 */
export const MAY_IMPORT = {
  core: [],
  contract: [],
  api: ['contract', 'core'],
  store: ['api', 'contract', 'core'],
  copy: [],
  ui: ['copy'],
  features: ['core', 'store', 'copy', 'ui'],
  app: ['core', 'api', 'store', 'copy', 'ui', 'features'],
};
/**
 * Single files a layer may import from a layer it may not otherwise import:
 * store/ makes server text (src/copy/server.ts) but never chooses wording.
 * @type {Record<string, Record<string, string[]>>}
 */
const MAY_IMPORT_FILES = {
  store: { copy: ['./server.ts'] },
};
const LAYERS = Object.keys(MAY_IMPORT);

/** @type {{ target: string, from: string[], except?: string[], message: string }[]} */
export const layerZones = LAYERS.flatMap((layer) => {
  const allowed = MAY_IMPORT[layer] ?? [];
  const partial = Object.keys(MAY_IMPORT_FILES[layer] ?? {});
  const from = LAYERS.filter(
    (other) => other !== layer && !allowed.includes(other) && !partial.includes(other),
  );
  return from.length === 0
    ? []
    : [
        {
          target: `./src/${layer}`,
          from: from.map((other) => `./src/${other}`),
          message: `src/${layer}/ may import only ${allowed.length ? allowed.map((l) => `${l}/`).join(', ') : 'itself'} (ADR-001 rule 1).`,
        },
      ];
});
for (const [layer, parts] of Object.entries(MAY_IMPORT_FILES)) {
  for (const [other, files] of Object.entries(parts)) {
    layerZones.push({
      target: `./src/${layer}`,
      from: [`./src/${other}`],
      except: files,
      message: `src/${layer}/ may import only ${files.join(', ')} from src/${other}/ (dashboard-spec 6.3).`,
    });
  }
}
// Components (.tsx) read view models, never the wire contract or the API client.
layerZones.push({
  target: './src/**/*.tsx',
  from: ['./src/contract', './src/api'],
  message:
    'Components (.tsx) never import src/contract/ or src/api/; they read view models from store/ (ADR-001 rule 1).',
});

/** store/ imports api/ for its types only: one definition of each record, no api/ value. */
export const storeTypeOnlyApi = {
  patterns: [
    {
      regex: '(^|/)api/',
      allowTypeImports: true,
      message: 'store/ imports api/ for types only (`import type`); app/ wires api/ values in.',
    },
  ],
};

// ------------------------------------------------------- browser and network
export const NETWORK_GLOBALS = ['fetch', 'WebSocket', 'XMLHttpRequest', 'EventSource'];
/**
 * Only src/api/ talks to the server: the bare names and the same names read
 * from window, self or globalThis (`new window.EventSource(...)`, F1-01).
 */
export const networkOutsideApi = {
  globals: NETWORK_GLOBALS.map((name) => ({
    name,
    message: 'Only src/api/ talks to the server (ADR-001 rule 1).',
  })),
  checkGlobalObject: true,
};

const coreMessage = 'src/core/ is pure TypeScript: no React, DOM or browser APIs (ADR-001 rule 1).';
/** core/ sees no browser at all (bare or read from a global object). */
const coreGlobalList = [
  'window',
  'self',
  'globalThis',
  'document',
  'navigator',
  'location',
  'history',
  'localStorage',
  'sessionStorage',
  'indexedDB',
  'HTMLElement',
  'Element',
  'Node',
  'Event',
  'EventTarget',
  'CustomEvent',
  'MutationObserver',
  'requestAnimationFrame',
  'matchMedia',
  'getComputedStyle',
  ...NETWORK_GLOBALS,
].map((name) => ({ name, message: coreMessage }));
export const coreGlobals = { globals: coreGlobalList, checkGlobalObject: true };
export const coreImports = {
  paths: ['react', 'react-dom', 'zod'].map((name) => ({ name, message: coreMessage })),
  patterns: [{ group: ['react/*', 'react-dom/*'], message: coreMessage }],
};

const htmlStringMessage =
  'React renders the page; no HTML strings (srcDoc, dangerouslySetInnerHTML) (ADR-001 rule 5).';
/**
 * The HTML-string sinks that are syntax rather than a property read: the JSX
 * srcDoc attribute and a dangerouslySetInnerHTML key, as in createElement props
 * (ADR Amendment 2). A coarse ban on the name, wherever it appears in src/.
 */
export const htmlStringSyntax = [
  { selector: "JSXAttribute[name.name='srcDoc']", message: htmlStringMessage },
  { selector: "Property[key.name='dangerouslySetInnerHTML']", message: htmlStringMessage },
];
/**
 * no-restricted-syntax is one rule: an entry that sets it replaces every earlier
 * entry's selectors (ADR-R2-01). core/ therefore carries the HTML-string selectors
 * itself, next to its JSX ban, and the src/ entry carries them for everything else.
 */
export const coreSyntax = [
  { selector: 'JSXElement', message: coreMessage },
  { selector: 'JSXFragment', message: coreMessage },
  ...htmlStringSyntax,
];

/** React renders the page: no HTML strings into the DOM. */
export const domTextSinks = [
  'innerHTML',
  'outerHTML',
  'srcdoc',
  'insertAdjacentHTML',
  'insertAdjacentText',
  'createContextualFragment',
  'write',
  'writeln',
].map((property) => ({
  property,
  message: 'React renders the page; text comes from src/copy/ (ADR-001 rule 5).',
}));

const fromServerPattern = {
  group: ['**/copy/server.ts'],
  importNames: ['fromServer'],
  message: 'Only store selectors (src/store/selectors/) make ServerText (dashboard-spec 6.3).',
};
const copyEntryPattern = {
  group: ['**/copy/entry.ts', '**/copy/entry'],
  message:
    'Wording is chosen only in the catalogue (src/copy/): import entries from it, not its constructor (ADR-001 rule 5).',
};
/** src/ outside selectors and copy/: neither makes server text nor chooses wording. */
export const srcRestrictedImports = { patterns: [fromServerPattern, copyEntryPattern] };
/** Store selectors make server text, never wording. */
export const selectorRestrictedImports = { patterns: [copyEntryPattern] };
/**
 * The catalogue chooses wording, never server text. Inside copy/ the file is
 * also reached by relative paths ('./server.ts', '../server.ts'), which the
 * copy/server.ts pattern above does not name (ADR-R2-03).
 */
export const copyRestrictedImports = {
  patterns: [fromServerPattern, { ...fromServerPattern, group: ['**/server.ts', '**/server'] }],
};

/**
 * The homes of the type-only brands: only they may assert their brand
 * (ADR-001 rule 2). Everywhere else in src/ a type assertion is refused.
 */
export const BRAND_HOMES = [
  'src/core/evidence.ts',
  'src/core/gate.ts',
  'src/core/readability.ts',
  'src/core/guards.ts',
  'src/core/tokens.ts',
  'src/core/vocabulary.ts',
  'src/copy/server.ts',
  'src/copy/entry.ts',
];
