import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: 4247,
  testMatch: 'runs-entry-parity.spec.ts',
  buildDir: 'playwright-report/runs-entry-parity-build',
  outputDir: '../playwright-report/runs-entry-parity-e2e',
});
