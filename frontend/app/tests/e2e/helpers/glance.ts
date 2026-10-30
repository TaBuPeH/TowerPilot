import { expect, type Page, type Request } from '@playwright/test';
import { expectAppStyles } from './helpers.ts';

/** A visible text inventory, not a universal readability score. */
export interface GlanceWords {
  readonly total: number;
  readonly regions: Readonly<Record<string, number>>;
  readonly nodes: readonly {
    readonly text: string;
    readonly words: number;
    readonly region: string;
    readonly element: string;
  }[];
  readonly clipped: readonly { readonly element: string; readonly text: string }[];
}

/**
 * Browser-evaluable owner of visible-word measurement. Pass it directly to
 * page.evaluate(measureVisibleWords, 'main'); no module closures are required.
 *
 * Count whitespace-delimited tokens containing a Unicode letter or number.
 * Adjacent inline text is joined before tokenizing, so splitting a word across
 * spans cannot change its cost. Layout blocks and form controls separate words.
 * Native input values and selected option labels count; hidden option choices,
 * closed detail bodies and screen-reader-only text do not. aria-hidden alone
 * does not hide drawn text. Below-fold and clipped text stay in the inventory;
 * clipping is reported separately so truncation cannot earn a reduction.
 */
export function measureVisibleWords(rootSelector = 'main'): GlanceWords {
  const root = document.querySelector(rootSelector);
  if (root === null) throw new Error(`Missing measurement root: ${rootSelector}`);
  const nodes: {
    text: string;
    words: number;
    region: string;
    element: string;
  }[] = [];
  const clipped: { element: string; text: string }[] = [];
  const clippedElements = new Set<Element>();
  let buffer = '';
  let bufferRegion = '';
  let bufferElement = '';

  const describe = (element: Element) =>
    `${element.tagName.toLowerCase()}${element.id === '' ? '' : `#${element.id}`}`;
  const normalize = (value: string) => value.replace(/\s+/gu, ' ').trim();
  const flush = () => {
    const text = normalize(buffer);
    const words = text.split(/\s+/u).filter((token) => /[\p{L}\p{N}]/u.test(token)).length;
    if (words > 0) nodes.push({ text, words, region: bufferRegion, element: bufferElement });
    buffer = '';
  };
  const regionOf = (element: Element) => {
    const region = element.closest('[data-region]');
    return (
      region?.getAttribute('data-region') ??
      element.closest('section[aria-labelledby]')?.getAttribute('aria-labelledby') ??
      'ungrouped'
    );
  };
  const append = (text: string, element: Element) => {
    const region = regionOf(element);
    if (region !== bufferRegion) flush();
    if (buffer === '') bufferElement = describe(element);
    bufferRegion = region;
    buffer += text;
  };
  const hidden = (element: Element) => {
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.contentVisibility === 'hidden') return true;
    // Check ancestors too: descendants of a clipped .sr-only wrapper can have
    // ordinary dimensions themselves. Ignore viewport position intentionally.
    for (let parent: Element | null = element; parent !== null; parent = parent.parentElement) {
      const parentStyle = getComputedStyle(parent);
      if (
        Number(parentStyle.opacity) === 0 ||
        parentStyle.display === 'none' ||
        parentStyle.contentVisibility === 'hidden'
      )
        return true;
      const rect = parent.getBoundingClientRect();
      const clip = parentStyle.getPropertyValue('clip').replace(/\s+/gu, '');
      if (
        clip === 'rect(0px,0px,0px,0px)' ||
        parentStyle.clipPath === 'inset(50%)' ||
        (rect.width <= 1 && rect.height <= 1 && ['hidden', 'clip'].includes(parentStyle.overflow))
      )
        return true;
    }
    return false;
  };
  const noteClipping = (element: Element) => {
    const style = getComputedStyle(element);
    if (
      !clippedElements.has(element) &&
      element.clientWidth > 1 &&
      ((element.scrollWidth > element.clientWidth + 1 &&
        ['hidden', 'clip'].includes(style.overflowX)) ||
        (element.scrollHeight > element.clientHeight + 1 &&
          ['hidden', 'clip'].includes(style.overflowY)))
    ) {
      clippedElements.add(element);
      clipped.push({ element: describe(element), text: normalize(element.textContent) });
    }
  };
  const visit = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      const parent = node.parentElement;
      if (parent === null || hidden(parent)) return;
      const style = getComputedStyle(parent);
      if (style.visibility !== 'visible') return;
      const range = document.createRange();
      range.selectNodeContents(node);
      if (![...range.getClientRects()].some((rect) => rect.width > 0 && rect.height > 0)) return;
      append(node.nodeValue ?? '', parent);
      return;
    }
    if (!(node instanceof Element) || hidden(node)) return;
    if (node.matches('script, style, noscript, template, option')) return;
    noteClipping(node);
    const display = getComputedStyle(node).display;
    const separates =
      /^(block|flex|grid|flow-root|list-item|table(?:-.+)?)$/u.test(display) ||
      node.matches('br, button, input, select, textarea');
    if (separates) flush();
    if (node.matches('input, select, textarea') && getComputedStyle(node).visibility !== 'visible')
      return;
    if (node instanceof HTMLSelectElement) {
      append([...node.selectedOptions].map((option) => option.label).join(' '), node);
    } else if (node instanceof HTMLInputElement) {
      if (!['hidden', 'checkbox', 'radio', 'range', 'color', 'file'].includes(node.type)) {
        append(node.value === '' ? node.placeholder : node.value, node);
      }
    } else if (node instanceof HTMLTextAreaElement) {
      append(node.value === '' ? node.placeholder : node.value, node);
    } else if (node instanceof HTMLDetailsElement && !node.open) {
      const summary = [...node.children].find(
        (child) => child instanceof HTMLElement && child.tagName === 'SUMMARY',
      );
      if (summary !== undefined) visit(summary);
    } else {
      for (const child of node.childNodes) visit(child);
    }
    if (separates) flush();
  };
  visit(root);
  flush();
  const regions: Record<string, number> = {};
  for (const node of nodes) regions[node.region] = (regions[node.region] ?? 0) + node.words;
  return { total: nodes.reduce((sum, node) => sum + node.words, 0), regions, nodes, clipped };
}

export const GLANCE_TIME = '2026-10-29T18:00:00.000Z';

/** Same readiness boundary for the fixed-fixture comparison and its regression checks. */
export async function openGlanceScenario(
  page: Page,
  scenario: string,
  width: number,
  origin = '',
): Promise<readonly string[]> {
  const pending = new Set<Request>();
  const completed = new Set<string>();
  const failures: string[] = [];
  const isApi = (request: Request) => {
    const pathname = new URL(request.url()).pathname;
    return pathname.startsWith('/api/') && !pathname.startsWith('/api/stream');
  };
  const onRequest = (request: Request) => {
    if (isApi(request)) pending.add(request);
  };
  const onFinished = (request: Request) => {
    if (pending.delete(request)) {
      const endpoint = new URL(request.url()).pathname;
      completed.add(endpoint);
      // Initial context fencing can abort a read and immediately replace it.
      // Only a completed replacement clears that endpoint's transport failure.
      for (let index = failures.length - 1; index >= 0; index -= 1) {
        if (failures[index] === endpoint) failures.splice(index, 1);
      }
    }
  };
  const onFailed = (request: Request) => {
    if (pending.delete(request)) failures.push(new URL(request.url()).pathname);
  };
  const onError = (error: Error) => failures.push(error.message);
  page.on('request', onRequest);
  page.on('requestfinished', onFinished);
  page.on('requestfailed', onFailed);
  page.on('pageerror', onError);
  try {
    await page.clock.setFixedTime(new Date(GLANCE_TIME));
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${origin}/__fake/use?scenario=${encodeURIComponent(scenario)}`);
    await expect(page.locator('[data-region="control"] time')).toBeVisible();
    await expect(page.locator('[data-region="needsYou"]')).toBeVisible();
    await expect(page.locator('[data-region="startNext"]')).toBeVisible();
    await expectAppStyles(page);
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    // Dashboard readiness comes in /api/runs; /api/readiness is checked on Start.
    const required = ['/api/status', '/api/config', '/api/runs', '/api/battles/active'];
    let previous = '';
    let unchanged = 0;
    await expect
      .poll(
        async () => {
          const current = JSON.stringify(await page.evaluate(measureVisibleWords, 'main'));
          unchanged = current === previous && pending.size === 0 ? unchanged + 1 : 0;
          previous = current;
          return required.every((endpoint) => completed.has(endpoint)) && unchanged >= 3;
        },
        {
          timeout: 15_000,
          intervals: [100],
          message: 'Dashboard reads and visible inventory settle',
        },
      )
      .toBe(true);
    expect(failures, 'No failed requests or browser errors before measurement').toEqual([]);
    return [...completed].sort();
  } finally {
    page.off('request', onRequest);
    page.off('requestfinished', onFinished);
    page.off('requestfailed', onFailed);
    page.off('pageerror', onError);
  }
}
