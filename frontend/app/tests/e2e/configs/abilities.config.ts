import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_ABILITIES_E2E_PORT'] ?? '4197'),
  testMatch: process.env['TP_ABILITIES_FULL_E2E'] === '1' ? '*.spec.ts' : 'abilities.spec.ts',
  buildDir: 'playwright-report/abilities-build',
  outputDir: '../playwright-report/abilities-e2e',
  fullyParallel: true,
});
