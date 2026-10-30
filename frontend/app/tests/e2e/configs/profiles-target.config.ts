import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4223,
  testMatch: 'profiles-target.spec.ts',
  buildDir: 'playwright-report/profiles-target-build',
  outputDir: '../playwright-report/profiles-target-e2e',
  webServerTimeout: 60_000,
});
