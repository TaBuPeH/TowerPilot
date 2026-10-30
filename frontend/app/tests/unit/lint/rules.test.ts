// @vitest-environment node
import { APP } from '../../support/paths.ts';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { Linter, RuleTester } from 'eslint';
import { configs as tsConfigs, parser } from 'typescript-eslint';
import { describe, expect, it } from 'vitest';
import towerPilot from '../../../eslint-rules/index.js';

/**
 * The lint policy that survived the F1 trim (eslint-rules/policy.js), one
 * probe per rule, linted with the app's own eslint.config.js (type-aware rules
 * off: the probes are not files of the TypeScript project), plus RuleTester
 * cases for the one custom rule. Guardrails against honest mistakes only.
 */

// Loaded at run time: the config is a Node module outside the test program's type check.
const { default: config } = (await import(
  pathToFileURL(path.join(APP, 'eslint.config.js')).href
)) as {
  readonly default: Linter.Config[];
};
const linter = new Linter({ cwd: APP });
const untyped = [
  ...config,
  tsConfigs.disableTypeChecked,
  { rules: { 'tower-pilot/no-string-jsx-child': 'off' } },
];

/** The rule ids a probe at `file` (relative to the app) reports. */
function lint(file: string, code: string): string[] {
  return linter
    .verify(code, untyped, { filename: path.join(APP, file) })
    .map((message) => message.ruleId ?? `fatal: ${message.message}`);
}

/** The `ruleId: message` pairs a probe at `file` reports, to tell two selectors of one rule apart. */
function lintMessages(file: string, code: string): string[] {
  return linter
    .verify(code, untyped, { filename: path.join(APP, file) })
    .map((message) => `${message.ruleId ?? 'fatal'}: ${message.message}`);
}

// The first lint of a run loads the TypeScript resolver: seconds on a loaded machine, not a hang.
describe('the layer zones (ADR-001 rule 1)', { timeout: 30_000 }, () => {
  it('refuse a component importing api/, and core/ importing store/', () => {
    expect(
      lint('src/features/dashboard/Probe.tsx', "export { ENDPOINTS } from '../../api/wire.ts';\n"),
    ).toContain('import-x/no-restricted-paths');
    expect(lint('src/core/probe.ts', "export { RESOURCE } from '../store/types.ts';\n")).toContain(
      'import-x/no-restricted-paths',
    );
  });

  it('let store/ import api/ types, and only types', () => {
    expect(
      lint('src/store/probe.ts', "export type { PageContext } from '../api/context.ts';\n"),
    ).toEqual([]);
    expect(
      lint(
        'src/store/probe.ts',
        "import { createSequence } from '../api/sequence.ts';\nexport const s = createSequence();\n",
      ),
    ).toContain('@typescript-eslint/no-restricted-imports');
  });

  it('keep core/ free of React and the browser', () => {
    expect(
      lint('src/core/probe.ts', "import { useState } from 'react';\nexport { useState };\n"),
    ).toContain('no-restricted-imports');
    expect(lint('src/core/probe.ts', 'export const title = document.title;\n')).toContain(
      'no-restricted-globals',
    );
  });
});

describe('no wording in components (ADR-001 rule 5)', () => {
  it('refuses JSX text', () => {
    expect(
      lint('src/features/dashboard/Probe.tsx', 'export const P = () => <p>Hello</p>;\n'),
    ).toContain('react/jsx-no-literals');
  });

  it('lets only store selectors make server text', () => {
    const code =
      "import { fromServer } from '../../copy/server.ts';\nexport const s = fromServer('name', 'x');\n";
    expect(lint('src/store/selectors/probe.ts', code)).toEqual([]);
    expect(lint('src/store/fences/probe.ts', code)).toContain('no-restricted-imports');
  });

  it('keeps fromServer out of copy/ however the file is named (ADR-R2-03)', () => {
    for (const [file, from] of [
      ['src/copy/probe.ts', './server.ts'],
      ['src/copy/probe.ts', './server'],
      ['src/copy/dashboard/probe.ts', '../server.ts'],
      ['src/copy/dashboard/probe.ts', '../../copy/server.ts'],
    ] as const) {
      expect(
        lint(file, `import { fromServer } from '${from}';\nexport { fromServer };\n`),
      ).toContain('no-restricted-imports');
    }
    // The branded type is not the maker: copy/ may still name ServerText.
    expect(
      lint(
        'src/copy/probe.ts',
        "import type { ServerText } from './server.ts';\nexport type S = ServerText;\n",
      ),
    ).toEqual([]);
  });

  it('refuses dangerouslySetInnerHTML and HTML strings into the DOM', () => {
    expect(
      lint(
        'src/features/dashboard/Probe.tsx',
        'export const P = (html: { __html: string }) => <div dangerouslySetInnerHTML={html} />;\n',
      ),
    ).toContain('react/no-danger');
    expect(
      lint(
        'src/ui/probe.ts',
        "export function set(node: HTMLElement) {\n  node.innerHTML = '<b>x</b>';\n}\n",
      ),
    ).toContain('no-restricted-properties');
  });
});

describe('no HTML strings (ADR Amendment 2, coarse bans by name)', () => {
  const syntax = 'no-restricted-syntax';
  it('refuses the JSX srcDoc attribute', () => {
    expect(
      lint(
        'src/features/dashboard/Probe.tsx',
        'export const P = (html: string) => <iframe title="x" srcDoc={html} />;\n',
      ),
    ).toContain(syntax);
  });

  it('refuses a dangerouslySetInnerHTML key outside JSX, as in createElement props', () => {
    expect(
      lint(
        'src/app/probe.ts',
        "export const props = { dangerouslySetInnerHTML: { __html: '<b>x</b>' } };\n",
      ),
    ).toContain(syntax);
  });

  it('refuses the srcdoc property and createContextualFragment', () => {
    expect(
      lint(
        'src/ui/probe.ts',
        "export function set(frame: HTMLIFrameElement) {\n  frame.srcdoc = '<b>x</b>';\n}\n",
      ),
    ).toContain('no-restricted-properties');
    expect(
      lint(
        'src/ui/probe.ts',
        "export const f = (r: Range) => r.createContextualFragment('<b>x</b>');\n",
      ),
    ).toContain('no-restricted-properties');
  });

  it('puts the same bans in core/ and leaves its JSX ban on (ADR-R2-01)', () => {
    // A later entry that sets no-restricted-syntax replaces an earlier one, so core/ must
    // carry both sets: the JSX ban and the HTML-string selectors.
    expect(
      lintMessages('src/core/probe.tsx', 'export const P = () => <p />;\n').join('\n'),
    ).toMatch(/no-restricted-syntax: src\/core\/ is pure TypeScript/);
    expect(
      lintMessages(
        'src/core/probe.ts',
        "export const props = { dangerouslySetInnerHTML: { __html: 'x' } };\n",
      ).join('\n'),
    ).toMatch(/no-restricted-syntax: React renders the page; no HTML strings/);
    expect(
      lintMessages('src/core/probe.tsx', 'export const P = () => <iframe srcDoc="x" />;\n').join(
        '\n',
      ),
    ).toMatch(/no-restricted-syntax: React renders the page; no HTML strings/);
  });
});

describe('no type assertions outside the brand homes (ADR-001 rule 2)', () => {
  const code = "export const x = JSON.parse('1') as number;\n";
  it('refuses an assertion in application code', () => {
    expect(lint('src/store/probe.ts', code)).toContain(
      '@typescript-eslint/consistent-type-assertions',
    );
  });
  it('lets a brand home make its brand', () => {
    expect(lint('src/core/evidence.ts', code)).toEqual([]);
  });
});

describe('the network only from api/ and no code from strings', () => {
  it('refuses fetch, WebSocket, XMLHttpRequest and EventSource outside api/', () => {
    for (const call of [
      "fetch('/api/status')",
      "new WebSocket('ws://x')",
      'new XMLHttpRequest()',
      "new EventSource('/api/events')",
    ]) {
      expect(lint('src/store/probe.ts', `export const r = ${call};\n`)).toContain(
        'no-restricted-globals',
      );
    }
    expect(lint('src/api/probe.ts', "export const r = fetch('/api/status');\n")).toEqual([]);
  });

  it('refuses the same names read from window, self or globalThis outside api/ (F1-01)', () => {
    for (const call of [
      "new window.EventSource('/api/events')",
      "window.fetch('/api/status')",
      "new globalThis.WebSocket('ws://x')",
      'new self.XMLHttpRequest()',
    ]) {
      expect(lint('src/store/probe.ts', `export const r = ${call};\n`)).toContain(
        'no-restricted-globals',
      );
    }
    expect(lint('src/api/probe.ts', "export const r = window.fetch('/api/status');\n")).toEqual([]);
  });

  it('refuses eval and new Function', () => {
    expect(lint('src/store/probe.ts', "export const r: unknown = eval('1');\n")).toContain(
      'no-eval',
    );
    expect(lint('src/store/probe.ts', "export const f = new Function('return 1');\n")).toContain(
      'no-new-func',
    );
  });
});

// The custom rule, type-aware, through ESLint's RuleTester.
RuleTester.describe = describe;
// The first case builds the type-aware project for the whole app; under full-suite load that
// alone can pass vitest's 5 s default, so each case gets the same 30 s as the zone checks.
RuleTester.it = (text: string, method: () => void) => {
  it(text, method, 30_000);
};
RuleTester.itOnly = it.only;
const tester = new RuleTester({
  languageOptions: {
    parser,
    parserOptions: {
      projectService: { allowDefaultProject: ['*.tsx'], defaultProject: 'tsconfig.json' },
      tsconfigRootDir: APP,
      ecmaFeatures: { jsx: true },
    },
  },
});
const brand =
  'declare const brand: unique symbol;\ntype CopyText = string & { readonly [brand]: true };\n';
// The same spelling as src/copy/server.ts: the rule knows ServerText by its brand symbol.
const serverBrand =
  "declare const serverBrand: unique symbol;\ntype ServerText = string & { readonly [serverBrand]: 'name' };\n";
const noStringJsxChild = towerPilot.rules?.['no-string-jsx-child'];
if (noStringJsxChild === undefined)
  throw new Error('tower-pilot/no-string-jsx-child is not registered');
tester.run('no-string-jsx-child', noStringJsxChild, {
  valid: [
    {
      filename: 'probe.tsx',
      code: `${brand}declare const t: CopyText;\nexport const P = () => <p>{t}</p>;\n`,
    },
    {
      filename: 'probe.tsx',
      code: 'declare const n: number;\nexport const P = () => <p>{n}</p>;\n',
    },
    { filename: 'probe.tsx', code: 'export const P = () => <p>{<b />}</p>;\n' },
    {
      filename: 'probe.tsx',
      code: `${brand}declare const t: readonly CopyText[];\nexport const P = () => <p>{t}</p>;\n`,
    },
    {
      // ui/ServerValue.tsx, the one place server text renders (eslint.config.js sets the option).
      filename: 'probe.tsx',
      code: `${serverBrand}declare const v: ServerText;\nexport const P = () => <bdi>{v}</bdi>;\n`,
      options: [{ allowServerText: true }],
    },
  ],
  invalid: [
    {
      filename: 'probe.tsx',
      code: 'declare const s: string;\nexport const P = () => <p>{s}</p>;\n',
      errors: [{ messageId: 'plain' }],
    },
    {
      filename: 'probe.tsx',
      code: `${brand}declare const s: CopyText | string | null;\nexport const P = () => <>{s}</>;\n`,
      errors: [{ messageId: 'plain' }],
    },
    {
      // F1-02: React renders string arrays and iterables too.
      filename: 'probe.tsx',
      code: 'export function P({ lines }: { readonly lines: readonly string[] }) {\n  return <span>{lines}</span>;\n}\n',
      errors: [{ messageId: 'plain' }],
    },
    {
      filename: 'probe.tsx',
      code: 'declare const lines: Iterable<string>;\nexport const P = () => <span>{lines}</span>;\n',
      errors: [{ messageId: 'plain' }],
    },
    {
      // F1-02 (review R06): a structural iterable of strings, not a named Iterable.
      filename: 'probe.tsx',
      code: 'declare const lines: { [Symbol.iterator](): Iterator<string> };\nexport const P = () => <span>{lines}</span>;\n',
      errors: [{ messageId: 'plain' }],
    },
    {
      // F1-03: server text renders only through ServerValue, even when it equals catalogue text.
      filename: 'probe.tsx',
      code: `${serverBrand}export function P({ value }: { readonly value: ServerText }) {\n  return <span>{value}</span>;\n}\n`,
      errors: [{ messageId: 'server' }],
    },
  ],
});
