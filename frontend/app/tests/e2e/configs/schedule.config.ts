import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_SCHEDULE_E2E_PORT'] ?? '4192'),
  buildDir: 'playwright-report/schedule-build',
  outputDir: 'playwright-report/schedule-e2e',
});
