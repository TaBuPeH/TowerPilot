import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4220,
  testMatch: 'runs-shopping-saved.spec.ts',
  buildDir: 'playwright-report/runs-shopping-saved-build',
  outputDir: '../playwright-report/runs-shopping-saved-e2e',
});
