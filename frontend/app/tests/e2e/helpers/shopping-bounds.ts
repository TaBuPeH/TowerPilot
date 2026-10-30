import { expect, type Locator } from '@playwright/test';
import { boundsDraft } from '../../support/shoppingBounds.ts';

const LABELS: Readonly<Record<string, string>> = {
  tap_limit: 'Tap attempts this battle',
  weight: 'Weight',
  from_wave: 'From wave',
  until_wave: 'Until wave (exclusive)',
};

function fields(editor: Locator, name: string) {
  return Object.entries(boundsDraft(name)).map(([key, value]) => {
    const [, id = '', field = ''] = key.split(':');
    return {
      input: editor
        .locator(`.tp-shop__entry[data-upgrade="${id}"]`)
        .getByLabel(LABELS[field] ?? '', { exact: true }),
      raw: String(value),
    };
  });
}

export async function fillBoundsDraft(editor: Locator, name: string) {
  for (const { input, raw } of fields(editor, name)) await input.fill(raw);
}

export async function expectBoundsDraft(editor: Locator, name: string) {
  for (const { input, raw } of fields(editor, name)) await expect(input).toHaveValue(raw);
}
