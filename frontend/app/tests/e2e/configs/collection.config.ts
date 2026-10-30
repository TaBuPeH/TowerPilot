import { isolatedConfig } from './isolated-config.ts';

/**
 * The Collection page's browser checks on their own port and build, so they can
 * run beside other authors' Playwright runs (the default config runs them too).
 */
export default isolatedConfig({
  port: Number(process.env['TP_COLLECTION_E2E_PORT'] ?? '4199'),
  testMatch: process.env['TP_PROFILES_FULL_E2E'] === '1' ? '*.spec.ts' : 'collection.spec.ts',
  buildDir: 'playwright-report/collection-build',
  outputDir: '../playwright-report/collection-e2e',
});
