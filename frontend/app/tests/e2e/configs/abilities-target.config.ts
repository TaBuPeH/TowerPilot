import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4226,
  testMatch: 'abilities-target.spec.ts',
  buildDir: 'playwright-report/abilities-target-build',
  outputDir: 'playwright-report/abilities-target-e2e',
  fullyParallel: true,
});
