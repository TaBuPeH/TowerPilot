import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_RUNTIME_E2E_PORT'] ?? '4205'),
  testMatch: 'runtime.spec.ts',
  buildDir: 'playwright-report/runtime-build',
  outputDir: '../playwright-report/runtime-e2e',
});
