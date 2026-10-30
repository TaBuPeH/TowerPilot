import { isolatedConfig } from './isolated-config.ts';

/**
 * The Profiles page's browser checks on their own port and build, so they can
 * run beside other authors' Playwright runs (the default config runs them too).
 */
export default isolatedConfig({
  port: Number(process.env['TP_PROFILES_E2E_PORT'] ?? '4197'),
  testMatch: process.env['TP_PROFILES_FULL_E2E'] === '1' ? '*.spec.ts' : 'profiles.spec.ts',
  buildDir: 'playwright-report/profiles-build',
  outputDir: '../playwright-report/profiles-e2e',
  webServerTimeout: 60_000,
  buildFirst: true,
});
