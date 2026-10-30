import path from 'node:path';
import { APP } from '../../support/paths.ts';
import { defineConfig } from '@playwright/test';
import preview from './preview.config.ts';
/** Dashboard surfaces affected by the new aside, using the same isolated fixture build. */
export default defineConfig({
  ...preview,
  testMatch: ['dashboard-density.spec.ts', 'dashboard-a11y.spec.ts', 'dashboard-screens.spec.ts'],
  outputDir: path.join(APP, 'playwright-report/preview-dashboard-e2e'),
});
