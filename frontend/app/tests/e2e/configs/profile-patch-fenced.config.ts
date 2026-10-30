import { isolatedConfig } from './isolated-config.ts';

export default isolatedConfig({
  port: Number(process.env['TP_PROFILE_PATCH_FENCED_E2E_PORT'] ?? '4277'),
  testMatch: 'profile-patch-fenced.spec.ts',
  buildDir: 'playwright-report/profile-patch-fenced-build',
  outputDir: '../playwright-report/profile-patch-fenced-e2e',
  workers: 4,
  fullyParallel: true,
});
