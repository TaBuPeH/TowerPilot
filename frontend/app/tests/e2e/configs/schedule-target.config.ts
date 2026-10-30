import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4224,
  testMatch: 'schedule-target.spec.ts',
  buildDir: 'playwright-report/schedule-target-build',
  outputDir: 'playwright-report/schedule-target-e2e',
});
