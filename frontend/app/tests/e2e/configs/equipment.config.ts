import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_EQUIPMENT_E2E_PORT'] ?? '4195'),
  testMatch: process.env['TP_EQUIPMENT_FULL_E2E'] === '1' ? '*.spec.ts' : 'equipment.spec.ts',
  buildDir: 'playwright-report/equipment-build',
  outputDir: '../playwright-report/equipment-e2e',
  fullyParallel: true,
});
