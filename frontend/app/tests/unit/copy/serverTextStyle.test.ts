// @vitest-environment node
import { SRC } from '../../support/paths.ts';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { parse } from 'postcss';
import { describe, expect, it } from 'vitest';

/**
 * dashboard-spec 6.3.1: ServerValue's stylesheet keeps server text whole.
 * Messages and details keep their line breaks and wrap anywhere; names and
 * ids wrap anywhere; nothing in the stylesheet cuts or hides text. Rendered
 * layout in a browser (visible line breaks, wrapping at 390 px) is checked by
 * the R10 end-to-end tests (dashboard-spec 8.9), not here.
 */
const UI = path.join(SRC, 'ui');
const CSS = readFileSync(path.join(UI, 'ServerValue.css'), 'utf8');

/** Declarations per selector; quotes dropped and spaces collapsed in selectors. */
function declarations(css: string): Map<string, Map<string, string>> {
  const found = new Map<string, Map<string, string>>();
  parse(css).walkRules((rule) => {
    for (const selector of rule.selectors) {
      const key = selector.replace(/["']/g, '').replace(/\s+/g, ' ').trim();
      const into = found.get(key) ?? new Map<string, string>();
      rule.walkDecls((declaration) => {
        into.set(declaration.prop.toLowerCase(), declaration.value.trim());
      });
      found.set(key, into);
    }
  });
  return found;
}

/** Properties that could cut, clamp or hide text. */
const HIDING = new Set([
  'overflow',
  'overflow-x',
  'overflow-y',
  'text-overflow',
  '-webkit-line-clamp',
  'line-clamp',
  'max-height',
  'height',
  'display',
  'visibility',
  'clip',
  'clip-path',
]);

describe('ServerValue stylesheet', () => {
  const rules = declarations(CSS);

  it('reads declarations per selector and skips comments (discriminating control)', () => {
    const sample = declarations(
      ".a[data-kind='x'] b, .c { white-space: normal } /* .d { white-space: pre-wrap } */",
    );
    expect(sample.get('.a[data-kind=x] b')?.get('white-space')).toBe('normal');
    expect(sample.get('.c')?.get('white-space')).toBe('normal');
    expect(sample.has('.d')).toBe(false);
  });

  it.each(['message', 'detail'])('keeps the line breaks of %s text and wraps it', (kind) => {
    const found = rules.get(`.tp-server[data-kind=${kind}] bdi`);
    expect(found?.get('white-space')).toBe('pre-wrap');
    expect(found?.get('overflow-wrap')).toBe('anywhere');
  });

  it.each(['name', 'id'])('wraps a long %s', (kind) => {
    expect(rules.get(`.tp-server[data-kind=${kind}] bdi`)?.get('overflow-wrap')).toBe('anywhere');
  });

  it('cuts or hides nothing', () => {
    const hiding: string[] = [];
    parse(CSS).walkDecls((declaration) => {
      if (HIDING.has(declaration.prop.toLowerCase())) {
        hiding.push(`${declaration.prop}: ${declaration.value}`);
      }
    });
    expect(hiding).toEqual([]);
  });

  it('is imported by ServerValue', () => {
    expect(readFileSync(path.join(UI, 'ServerValue.tsx'), 'utf8')).toContain(
      "import './ServerValue.css';",
    );
  });
});
