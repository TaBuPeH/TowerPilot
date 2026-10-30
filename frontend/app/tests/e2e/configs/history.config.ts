import { isolatedConfig } from './isolated-config.ts';

/** An isolated port/build keeps this author away from live services and concurrent authors. */
export default isolatedConfig({
  port: Number(process.env['TP_HISTORY_E2E_PORT'] ?? '4186'),
  testMatch: 'history*.spec.ts',
  buildDir: 'node_modules/.cache/history-build',
  outputDir: '../test-results/history-e2e',
  webServerTimeout: 60_000,
  buildFirst: true,
});
