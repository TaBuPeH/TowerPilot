import { isolatedConfig } from './isolated-config.ts';

/**
 * The Emulators page's browser checks on their own port and build, so they can
 * run beside other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/emulators-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_EMULATORS_E2E_PORT'] ?? '4203'),
  testMatch: 'emulators.spec.ts',
  buildDir: 'playwright-report/emulators-build',
  outputDir: '../playwright-report/emulators-e2e',
});
