import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4230,
  testMatch: 'runtime-config.spec.ts',
  buildDir: 'playwright-report/runtime-config-build',
  outputDir: '../playwright-report/runtime-config-e2e',
  workers: 2,
  jsonReport: 'playwright-report/runtime-config-results.json',
});
