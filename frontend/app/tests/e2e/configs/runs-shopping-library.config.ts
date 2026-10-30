import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4222,
  testMatch: 'runs-shopping-library.spec.ts',
  buildDir: 'playwright-report/runs-shopping-library-build',
  outputDir: '../playwright-report/runs-shopping-library-e2e',
  jsonReport: 'playwright-report/shopping-library-browser.json',
});
