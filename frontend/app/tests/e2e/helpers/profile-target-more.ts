import { expect, test, type Page } from '@playwright/test';
import { entries, own } from '../../../src/core/read.ts';
import { moreCase } from '../../support/profileTargetMore.ts';
import type { DashboardCase } from '../../support/dashboardFixture.ts';

/** Browser-local recorded server: avoids changing the shared fake server used by other workers. */
export async function moreWorld(page: Page, initial: DashboardCase) {
  let source = initial;
  let answer = moreCase('inactive_plan_deleted');
  const sent: unknown[] = [];
  let reads = 0;
  await page.route('**/api/profile-src/second', async (route) => {
    reads++;
    await route.fulfill({ status: source.status, json: source.body });
  });
  await page.route('**/api/profile-patch', async (route) => {
    sent.push(route.request().postDataJSON());
    if (answer.status === 200) {
      // Intermediate GET: exact receipt profile/revision inside the recorded source envelope.
      source = {
        ...initial,
        body: {
          ...Object.fromEntries(entries(initial.body)),
          ...Object.fromEntries(entries(answer.body)),
        },
      };
    }
    await route.fulfill({ status: answer.status, json: answer.body });
  });
  return {
    sent,
    source: (next: DashboardCase) => {
      source = next;
    },
    answer: (next: DashboardCase) => {
      answer = next;
    },
    reads: () => reads,
  };
}

export async function openMore(page: Page, width: number, hash: string, scenario: string) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/__fake/use?scenario=${scenario}`);
  await page.evaluate(() => {
    location.hash = '#profiles';
  });
  const picker = page.locator('[data-form="editing-target"]');
  await picker.getByRole('combobox').selectOption('second');
  await picker.getByRole('button', { name: 'Edit this profile', exact: true }).click();
  await expect(picker).toContainText('Editing:second');
  await page.evaluate((hash) => {
    location.hash = hash;
  }, hash);
}

export { moreCase, moreNames, moreSourceBefore } from '../../support/profileTargetMore.ts';
export const moreEntry = (page: Page, key: string) =>
  page.locator(`[data-run-entry] [data-entry-field="${key}"]`);

export function invalidSourceTests(
  editor: 'schedule' | 'abilities' | 'runs' | 'collection',
  proof: (page: Page, state: string, width: number) => Promise<void>,
) {
  const prefix =
    editor === 'schedule'
      ? 'PE3'
      : editor === 'abilities'
        ? 'PE5'
        : editor === 'runs'
          ? 'PE6'
          : 'PE1';
  for (const width of [1360, 1054, 390])
    for (const name of [
      'inactive_target_source_that_cannot_be_parsed_400',
      'inactive_target_source_that_is_not_a_mapping_400',
    ]) {
      test(`${prefix} F6-14c ${name} blocks ${editor} with the server reason at ${width}`, async ({
        page,
      }) => {
        const failed = moreCase(name);
        const world = await moreWorld(page, failed);
        await page.route('**/api/profile-files', async (route) => {
          const answer = moreCase(
            'profile_files_still_lists_the_target_whose_source_cannot_be_parsed',
          );
          await route.fulfill({ status: answer.status, json: answer.body });
        });
        await openMore(
          page,
          width,
          editor === 'runs' ? '#runs/saved/farm' : `#${editor}`,
          `profile_target_${editor === 'abilities' ? 'abilities_uw' : editor === 'runs' ? 'runs_saved' : editor === 'collection' ? 'collection_label' : 'schedule_saved'}`,
        );
        await expect(page.locator('main')).toContainText(
          'This profile could not be read. Writes are unavailable until a successful read.',
        );
        await expect(
          page
            .locator('[data-server-text="message"]')
            .filter({ hasText: String(own(failed.body, 'error')) })
            .first(),
        ).toBeVisible();
        expect(world.sent).toEqual([]);
        await proof(page, `f6-14c-${name}`, width);
      });
    }
}
