import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4227,
  testMatch: 'runs-entry-target.spec.ts',
  buildDir: 'playwright-report/runs-entry-target-build',
  outputDir: '../playwright-report/runs-entry-target-e2e',
});
