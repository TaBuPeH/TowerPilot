import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4228,
  testMatch: 'equipment-config.spec.ts',
  buildDir: 'playwright-report/equipment-config-build',
  outputDir: '../playwright-report/equipment-config-e2e',
  fullyParallel: true,
});
