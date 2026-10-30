import { isolatedConfig } from './isolated-config.ts';

/** Build first: npx vite build --outDir playwright-report/emulators-bs-build --emptyOutDir */
export default isolatedConfig({
  port: 4213,
  testMatch: [
    'emulators.spec.ts',
    'emulators.confirmations.spec.ts',
    'emulators.bluestacks.spec.ts',
  ],
  buildDir: 'playwright-report/emulators-bs-build',
  outputDir: 'test-results/emulators-bs-e2e',
  workers: 1,
});
