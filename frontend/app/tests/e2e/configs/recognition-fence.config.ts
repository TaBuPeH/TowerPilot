import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_RECOGNITION_FENCE_E2E_PORT'] ?? '4217'),
  testMatch: 'recognition-fence.spec.ts',
  buildDir: 'playwright-report/recognition-fence-build',
  outputDir: '../playwright-report/recognition-fence-e2e',
});
