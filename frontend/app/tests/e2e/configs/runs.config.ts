import { isolatedConfig } from './isolated-config.ts';

/**
 * The Runs page's browser checks on their own port and build, so they can run beside
 * other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/runs-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_RUNS_E2E_PORT'] ?? '4215'),
  testMatch: 'runs.spec.ts',
  buildDir: 'playwright-report/runs-build',
  outputDir: '../playwright-report/runs-e2e',
});
