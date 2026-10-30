import { isolatedConfig } from './isolated-config.ts';

/**
 * The Recognition advanced repairs' browser checks on their own port and build, so they can
 * run beside other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/recognition-advanced-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_RECOGNITION_ADVANCED_E2E_PORT'] ?? '4207'),
  testMatch: 'recognition-advanced.spec.ts',
  buildDir: 'playwright-report/recognition-advanced-build',
  outputDir: '../playwright-report/recognition-advanced-e2e',
});
