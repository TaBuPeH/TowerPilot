import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';
import { axeIds, bodiesTo, posts, WCAG_AA_TAGS } from './helpers/helpers.ts';
import { dashboardPagesCase } from '../support/dashboardFixture.ts';

const OUT =
  process.env['TP_F6_SCREENS_DIR'] ??
  path.resolve('../../backend/logs/reviews/frontend-f6-screens');
mkdirSync(OUT, { recursive: true });

for (const width of [1360, 1054, 390]) {
  for (const state of ['coin', 'refused', 'coded', 'uncoded']) {
    test(`RL4 ${state} at ${String(width)}: one guarded add, axe, reflow, targets and screenshot`, async ({
      page,
    }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => {
        errors.push(error.message);
      });
      await page.setViewportSize({ width, height: 900 });
      const coded = (name: string) => dashboardPagesCase(`run_templates_add_coded.${name}`);
      let source = coded('read_before_any_add');
      if (state === 'coded' || state === 'uncoded') {
        await page.route('**/api/profile-src/mine', (route) =>
          route.fulfill({ status: source.status, json: source.body }),
        );
        await page.route('**/api/run-templates/add', async (route) => {
          // Replay the unchanged recorded refusal against a valid fenced add. For id_taken
          // this models another client taking the identifier after the displayed source read.
          await route.fetch();
          const answer = coded(
            state === 'coded'
              ? 'id_taken_the_run_exists'
              : 'written_then_the_answer_failed_has_no_code',
          );
          await route.fulfill({ status: answer.status, json: answer.body });
        });
      }
      await page.goto(`/__fake/use?scenario=runs_add_${state === 'refused' ? 'refused' : 'coin'}`);
      await page.evaluate(() => {
        window.location.hash = '#runs';
      });
      const form = page.locator('[data-run-add]');
      const disclosure = form.locator('summary[data-focus-key="run-add:open"]');
      await disclosure.focus();
      await page.keyboard.press('Enter');
      await expect(form.getByLabel('Recipe')).toHaveValue('farm');
      await form.getByLabel('Run identifier').fill(state === 'uncoded' ? 'half_run' : 'fenced_one');
      await form.getByLabel('New run label').fill(state === 'uncoded' ? 'Half run' : 'Fenced one');
      await form.getByRole('button', { name: 'Save new run' }).focus();
      await expect(form.getByRole('button', { name: 'Save new run' })).toBeFocused();
      await form.getByRole('button', { name: 'Save new run' }).dblclick();
      if (state === 'uncoded') {
        await expect(form.getByText(/^Unknown: the add/)).toBeVisible();
        await expect(form.getByRole('button', { name: 'Save new run' })).toHaveAttribute(
          'aria-disabled',
          'true',
        );
        await form.getByRole('button', { name: 'Check profile' }).click();
        await expect(form.getByText(/^Unknown: the add/)).toBeVisible();
        await expect(form.locator('[data-server-text="message"]')).toHaveText(
          'the answer could not be built after the file was written',
        );
        await form.screenshot({
          path: path.join(OUT, `runs-add-uncoded-unknown-${String(width)}.png`),
        });
        source = coded('read_after_an_error_that_followed_the_write_holds_the_run');
        await form.getByRole('button', { name: 'Check profile' }).click();
      }
      await expect(
        form.getByText(
          state === 'coin' || state === 'uncoded'
            ? 'New run saved.'
            : 'Run was not added. Your draft is kept.',
        ),
      ).toBeVisible();
      const expected =
        state === 'uncoded'
          ? coded('written_then_the_answer_failed_has_no_code').request.body
          : dashboardPagesCase(
              'run_templates_add_fenced.added_with_the_matching_revision_and_context',
            ).request.body;
      // The coded-refusal replay uses the valid form's choices and the coded family's base revision.
      expect(await bodiesTo(page, '/api/run-templates/add')).toEqual([
        state === 'coded'
          ? {
              ...Object.fromEntries(Object.entries(expected as Record<string, unknown>)),
              expected_revision: '00000000000000000000000000000000000000000000000000000000000000e0',
            }
          : expected,
      ]);
      if (state === 'coded') {
        await expect(form.getByLabel('Run identifier')).toHaveValue('fenced_one');
        await expect(
          form.locator('[data-run-add-outcome] [data-server-text="message"]'),
        ).toHaveText('That run already exists. Choose a different identifier.');
        await expect(
          form.getByText(
            'That run identifier is already taken. Choose another; your draft is kept.',
          ),
        ).toBeVisible();
      }
      if (state === 'refused') {
        await expect(form.getByLabel('Run identifier')).toHaveValue('fenced_one');
        await expect(
          form.locator('[data-run-add-outcome] [data-server-text="message"]'),
        ).toHaveText(
          'Saved profile changed. Reload and reconcile the pending edit before saving again.',
        );
      }
      const violations = await axeIds(page, WCAG_AA_TAGS);
      expect(violations).toEqual([]);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        ),
      ).toBe(0);
      const small = await form.locator('button, input, select, textarea, summary, a').evaluateAll(
        (elements, viewport) =>
          elements
            .filter((element) => element.getClientRects().length > 0)
            .filter((element) => {
              const box = element.getBoundingClientRect();
              return (
                box.width < 24 ||
                box.height < (viewport === 390 && element.tagName === 'BUTTON' ? 44 : 24)
              );
            })
            .map((element) => element.outerHTML),
        width,
      );
      expect(small).toEqual([]);
      await form.screenshot({ path: path.join(OUT, `runs-add-${state}-${String(width)}.png`) });
      expect(
        (await posts(page)).filter((posted) => posted.path !== '/api/run-templates/add'),
      ).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
}
