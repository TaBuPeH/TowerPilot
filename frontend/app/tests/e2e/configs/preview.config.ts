import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_PREVIEW_E2E_PORT'] ?? '4273'),
  testMatch: 'preview.spec.ts',
  buildDir: 'playwright-report/preview-build',
  outputDir: '../playwright-report/preview-e2e',
  workers: 1,
  timeout: 30_000,
});
