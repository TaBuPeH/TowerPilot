import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4225,
  testMatch: 'collection-target.spec.ts',
  buildDir: 'playwright-report/collection-target-build',
  outputDir: '../playwright-report/collection-target-e2e',
});
