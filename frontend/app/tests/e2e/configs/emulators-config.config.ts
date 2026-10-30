import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4229,
  testMatch: 'emulators-config.spec.ts',
  buildDir: 'playwright-report/emulators-config-build',
  outputDir: '../playwright-report/emulators-config-e2e',
});
