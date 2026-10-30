import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4211,
  testMatch: 'collection.firstPolicy.spec.ts',
  buildDir: 'playwright-report/collection-fp-build',
  outputDir: '../playwright-report/collection-fp-e2e',
  workers: 1,
});
