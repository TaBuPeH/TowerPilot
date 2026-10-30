import { isolatedConfig } from './isolated-config.ts';

/**
 * The Recognition page's browser checks on their own port and build, so they can run
 * beside other authors' Playwright runs (the default config runs them too).
 * Build first: npx vite build --outDir playwright-report/recognition-build --emptyOutDir
 */
export default isolatedConfig({
  port: Number(process.env['TP_RECOGNITION_E2E_PORT'] ?? '4209'),
  testMatch: 'recognition.spec.ts',
  buildDir: 'playwright-report/recognition-build',
  outputDir: '../playwright-report/recognition-e2e',
});
