import { createRequire } from 'node:module';
import { expect, type Page, type Request } from '@playwright/test';

/** Shared steps of the Dashboard browser checks against the fake backend (tests/support/fake-backend/fake-backend.mjs). */

export interface Posted {
  readonly method: string;
  readonly path: string;
  readonly body: string;
}

/** Choose an app destination from the persistent sidebar or its narrow disclosure. */
export async function navigateApp(page: Page, label: string): Promise<void> {
  const navigation = page.getByRole('navigation', {
    name: 'Navigation',
    exact: true,
    includeHidden: true,
  });
  if (!(await navigation.isVisible())) {
    await page.getByRole('button', { name: 'Navigation', exact: true }).click();
  }
  const destination = navigation.getByRole('link', {
    name: label,
    exact: true,
    includeHidden: true,
  });
  await destination.click();
  // The narrow panel closes on selection; wait for the router before another navigation.
  await expect(destination).toHaveAttribute('aria-current', 'page');
}

/**
 * Open the Dashboard on a fake-backend scenario at `width`. `faults` names
 * parts made to fail while drawing (the session key app/ reads, X4 to X6).
 * Waits for the first answered status read unless `waitForRead` is false.
 */
export async function openScenario(
  page: Page,
  scenario: string,
  options: {
    readonly width?: number;
    readonly faults?: readonly string[];
    readonly waitForRead?: boolean;
  } = {},
): Promise<string[]> {
  const problems: string[] = [];
  page.on('pageerror', (error) => {
    problems.push(error.message);
  });
  if (options.faults !== undefined && options.faults.length > 0) {
    await page.addInitScript((faults) => {
      window.sessionStorage.setItem('tp.faults', faults);
    }, options.faults.join(','));
  }
  await page.setViewportSize({ width: options.width ?? 1360, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await expect(page.getByRole('heading', { level: 2, name: 'Control' })).toBeVisible();
  if (options.waitForRead !== false) {
    await expect(page.locator('[data-region="control"] time')).toBeVisible();
  }
  return problems;
}

/** The writes the fake backend recorded for this browser's session (diagnostics excluded). */
export async function posts(page: Page): Promise<Posted[]> {
  const answer = await page.request.get('/__fake/posts');
  const body = (await answer.json()) as { readonly posts: Posted[] };
  return body.posts;
}

/** The parsed bodies of the recorded writes to `path`. */
export async function bodiesTo(page: Page, path: string): Promise<unknown[]> {
  return (await posts(page))
    .filter((entry) => entry.path === path)
    .map((entry) => JSON.parse(entry.body) as unknown);
}

const AXE = createRequire(import.meta.url).resolve('axe-core/axe.min.js');

/** The WCAG 2.2 A and AA rule tags most page checks run axe with. */
export const WCAG_AA_TAGS: readonly string[] = [
  'wcag2a',
  'wcag2aa',
  'wcag21a',
  'wcag21aa',
  'wcag22aa',
];

/** Read metadata only; response.body() can refetch a body Chromium no longer retains. */
async function stylesheetRequest(request: Request, hrefs: ReadonlySet<string>) {
  let url: string | null = null;
  try {
    url = request.url();
    if (!hrefs.has(url)) return null;
    // Only ask for an already-started response. Never wait for pending headers. The
    // request's failure/responseEnd fields need prior event listeners, so omit them.
    const response = request.timing().responseStart >= 0 ? await request.response() : null;
    return response === null
      ? { url, responseMetadata: 'not observed' }
      : { url, status: response.status(), headers: response.headers() };
  } catch (error) {
    return { url, metadataError: String(error) };
  }
}

/**
 * Fail unless the built app stylesheet applies to the page. On an unstyled page axe and the
 * geometry checks report contrast, target-size and overflow faults the app does not have, so
 * every such check runs this first. Each linked stylesheet must have loaded with rules.
 */
export async function expectAppStyles(page: Page): Promise<void> {
  const state = await page.evaluate(() => ({
    url: location.href,
    readyState: document.readyState,
    clocks: {
      timeOrigin: performance.timeOrigin,
      now: performance.now(),
      dateNow: Date.now(),
    },
    sheets: [...document.querySelectorAll<HTMLLinkElement>('link[rel~="stylesheet"]')].map(
      (link) => {
        let rules = 0;
        let cssomError: string | null = null;
        try {
          rules = link.sheet?.cssRules.length ?? 0;
        } catch (error) {
          cssomError = String(error);
        }
        return {
          href: link.href,
          rules,
          hasSheet: link.sheet !== null,
          cssomError,
          disabled: link.disabled,
          media: link.media,
        };
      },
    ),
    resources: performance
      .getEntriesByType('resource')
      .filter(
        (entry) => entry instanceof PerformanceResourceTiming && entry.initiatorType === 'link',
      )
      .map((entry) => entry.toJSON() as unknown),
  }));
  const { sheets } = state;
  const app = sheets.filter((sheet) => /\/ui\/app\/assets\/[^/]+\.css$/u.test(sheet.href));
  if (app.length > 0 && sheets.every((sheet) => sheet.rules > 0)) return;
  // Metadata failure must not replace the primary CSSOM/resource evidence.
  const hrefs = new Set(sheets.map((sheet) => sheet.href));
  let retained;
  try {
    const requests = (
      await Promise.all((await page.requests()).map((request) => stylesheetRequest(request, hrefs)))
    ).filter((request) => request !== null);
    retained = { requests };
  } catch (error) {
    retained = { lookupError: String(error) };
  }
  throw new Error(
    `app stylesheet not loaded: ${JSON.stringify(state)}; ` +
      `retained request metadata: ${JSON.stringify(retained)}; ` +
      'request failure/completion events: not observed by this diagnostic; ' +
      'response bodies: not read (body retrieval can refetch); ' +
      'retention limits: page.requests() keeps only recent requests; resource timing has a ' +
      'finite buffer and entries may be absent, evicted or cleared. Missing entries do not ' +
      'prove no request or pending I/O. Resource times are relative to clocks.timeOrigin; ' +
      'clocks.dateNow is epoch milliseconds.',
  );
}

/** One axe violation as the page returns it (other fields pass through untyped). */
export interface AxeViolation {
  readonly id: string;
  readonly nodes: readonly { readonly target: readonly unknown[] }[];
}

/**
 * Run axe on the whole document once the app stylesheet is proven loaded. `tags` limits the
 * rules to those tags (runOnly); without it axe runs its default rules.
 */
export async function axeViolations(page: Page, tags?: readonly string[]): Promise<AxeViolation[]> {
  await expectAppStyles(page);
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async (values) => {
    const axe = Reflect.get(window, 'axe') as {
      run: (root: Document, options?: unknown) => Promise<{ violations: AxeViolation[] }>;
    };
    const result =
      values === null
        ? await axe.run(document)
        : await axe.run(document, { runOnly: { type: 'tag', values } });
    return result.violations;
  }, tags ?? null);
}

/** The ids of the axe violations. */
export async function axeIds(page: Page, tags?: readonly string[]): Promise<string[]> {
  return (await axeViolations(page, tags)).map((violation) => violation.id);
}

/** Each axe violation as `id (node count)`. */
export async function axeCounts(page: Page, tags?: readonly string[]): Promise<string[]> {
  return (await axeViolations(page, tags)).map(
    (violation) => `${violation.id} (${String(violation.nodes.length)})`,
  );
}
