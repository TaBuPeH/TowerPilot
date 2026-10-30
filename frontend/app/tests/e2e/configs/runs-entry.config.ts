import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4219,
  testMatch: 'runs-entry.spec.ts',
  buildDir: 'playwright-report/runs-entry-build',
  outputDir: '../playwright-report/runs-entry-e2e',
});
