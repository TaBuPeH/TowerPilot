// @vitest-environment node
import { identifier, stylesheets } from '../../support/css.ts';
import { SRC } from '../../support/paths.ts';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { AtRule, Declaration, parse, type Rule } from 'postcss';
import { describe, expect, it } from 'vitest';

/**
 * One surface grammar (council-tiles synthesis, commit 0): a Region is the only
 * outlined surface, a tile is a borderless fill, and a tile inside a tile
 * collapses to a divider. src/ui/ owns every surface; a stylesheet anywhere else
 * that gives one rule both an edge (a border or a radius) and a surface fill
 * (`var(--tp-color-surface*)`) is drawing its own card.
 *
 * ALLOWLIST holds the rules that did so when the grammar landed. It only
 * shrinks: a new surface fails, and so does an entry whose rule no longer draws
 * one (delete the entry when a page converts).
 */

/** Sides and corners of `border`; `border-color`, `-collapse` and `-spacing` draw nothing alone. */
const EDGE =
  /^border(-(top|right|bottom|left|block|inline|block-start|block-end|inline-start|inline-end))?(-width|-style)?$/;
const RADIUS = /^border(-[a-z-]+)?-radius$/;
const NO_EDGE = new Set(['0', '0px', 'none', '0 none', 'none 0', 'hidden']);
const SURFACE = /var\(\s*--tp-color-surface/;

/** The rule's selector, with the at-rules it sits in, whitespace normalised. */
function ruleKey(rule: Rule): string {
  const parts = [rule.selector.replace(/\s+/g, ' ').trim()];
  let parent: unknown = rule.parent;
  while (parent instanceof AtRule) {
    parts.unshift(`@${parent.name} ${parent.params.replace(/\s+/g, ' ').trim()}`);
    parent = parent.parent;
  }
  return parts.join(' > ');
}

/** Whether one declaration draws an edge (a visible border or a radius). */
function drawsEdge(declaration: Declaration): boolean {
  const prop = identifier(declaration.prop);
  const value = declaration.value.trim().toLowerCase();
  if (RADIUS.test(prop)) return value !== '0' && value !== '0px';
  return EDGE.test(prop) && !NO_EDGE.has(value);
}

/** Whether one declaration fills with a surface colour. */
function drawsFill(declaration: Declaration): boolean {
  const prop = identifier(declaration.prop);
  return (
    (prop === 'background' || prop === 'background-color') &&
    SURFACE.test(declaration.value.toLowerCase())
  );
}

/** Every rule (by key) that declares an edge and a surface fill together. */
export function surfaceRules(css: string): string[] {
  const found: string[] = [];
  parse(css).walkRules((rule) => {
    const declarations = rule.nodes.filter((node) => node instanceof Declaration);
    if (declarations.some(drawsEdge) && declarations.some(drawsFill)) found.push(ruleKey(rule));
  });
  return found;
}

/**
 * src/-relative stylesheet path (posix) to the surface rules it may still declare.
 * Most are page-local form controls and cards that the page conversions replace.
 */
const ALLOWLIST: Readonly<Record<string, readonly string[]>> = {
  'app/appShell.css': [
    '.tp-app-navigation',
    '.tp-app-navigation__toggle',
    ".tp-app-navigation a[aria-current='page']",
  ],
  'features/dashboard/dashboard.css': ['.tp-toast'],
  'features/dashboard/needsYou/needsYou.css': ['.tp-need__count'],
};

const UI = path.join(SRC, 'ui');
const outside = stylesheets(SRC)
  .filter((file) => !file.startsWith(UI + path.sep))
  .map((file) => [path.relative(SRC, file).split(path.sep).join('/'), file] as const);

describe('one surface grammar', () => {
  it('finds an edge plus a surface fill in any spelling, and nothing else (discriminating control)', () => {
    expect(
      surfaceRules(
        [
          '.card { border: 1px solid var(--tp-color-border); background: var(--tp-color-surface-inner); }',
          '.round { BORDER-RADIUS: 6px; background-color: VAR(--tp-color-surface); }',
          '.side { b\\6f rder-top: 1px solid red; background: var( --tp-color-surface-strong); }',
          '@media (max-width: 600px) { .narrow   .card { border-inline-start: 2px solid; background: var(--tp-color-surface-raised); } }',
          '.flat { border: 0; border-radius: 0; background: var(--tp-color-surface); }',
          '.none { border: none; background: var(--tp-color-surface); }',
          '.edge { border: 1px solid var(--tp-color-border); background: transparent; }',
          '.fill { background: var(--tp-color-surface-inner); border-color: red; }',
          '.page { border: 1px solid; background: var(--tp-color-page); }',
          '/* .commented { border: 1px solid; background: var(--tp-color-surface); } */',
        ].join('\n'),
      ),
    ).toEqual(['.card', '.round', '.side', '@media (max-width: 600px) > .narrow .card']);
  });

  it.each(outside)('%s draws no surface of its own beyond its allowlist', (name, file) => {
    const found = surfaceRules(readFileSync(file, 'utf8'));
    const allowed = ALLOWLIST[name] ?? [];
    expect(
      found.filter((key) => !allowed.includes(key)),
      'new surface: take it from src/ui (Region, .tp-tile) instead',
    ).toEqual([]);
    expect(
      allowed.filter((key) => !found.includes(key)),
      'no longer a surface: delete these allowlist entries',
    ).toEqual([]);
  });

  it('lists only stylesheets that exist outside src/ui', () => {
    const names = new Set(outside.map(([name]) => name));
    expect(Object.keys(ALLOWLIST).filter((name) => !names.has(name))).toEqual([]);
    expect(Object.keys(ALLOWLIST).filter((name) => ALLOWLIST[name]?.length === 0)).toEqual([]);
  });

  it('src/ui/ui.css keeps the grammar: an outlined Region, a borderless tile, a tile-in-tile divider', () => {
    const file = path.join(UI, 'ui.css');
    expect(existsSync(file)).toBe(true);
    const rules = new Map<string, Map<string, string>>();
    parse(readFileSync(file, 'utf8')).walkRules((rule) => {
      const declarations = rules.get(ruleKey(rule)) ?? new Map<string, string>();
      rule.walkDecls((declaration) => {
        declarations.set(identifier(declaration.prop), declaration.value.trim());
      });
      rules.set(ruleKey(rule), declarations);
    });
    const region = rules.get('.tp-region');
    expect(region?.get('border')).toMatch(/^1px solid /);
    const tile = rules.get('.tp-tile');
    expect(tile?.get('border')).toBe('0');
    expect(tile?.get('background')).toMatch(SURFACE);
    expect(tile?.get('border-radius')).toBe('var(--tp-radius-control)');
    const nested = rules.get('.tp-tile .tp-tile');
    expect(nested?.get('background')).toBe('none');
    expect(nested?.get('border-radius')).toBe('0');
    expect(nested?.get('border-block-start')).toMatch(/^1px solid /);
  });
});
