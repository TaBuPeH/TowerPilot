import path from 'node:path';
import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';
import { APP } from '../../support/paths.ts';
import { E2E_NODE } from '../helpers/runtime.ts';

type IsolatedOptions = Pick<
  PlaywrightTestConfig,
  'testMatch' | 'workers' | 'timeout' | 'fullyParallel'
> & {
  readonly port: number;
  readonly buildDir: string;
  readonly outputDir: string;
  readonly jsonReport?: string;
  readonly buildFirst?: boolean;
  readonly webServerTimeout?: number;
};

/** One fixture server per isolated browser run; callers retain their existing settings. */
export function isolatedConfig({
  port,
  buildDir,
  outputDir,
  jsonReport,
  buildFirst = false,
  webServerTimeout = 30_000,
  ...options
}: IsolatedOptions): PlaywrightTestConfig {
  const server = `${E2E_NODE} tests/support/fake-backend/fake-backend.mjs --port ${String(port)} --build-dir ${buildDir}`;
  return defineConfig({
    testDir: path.join(APP, 'tests/e2e'),
    forbidOnly: true,
    retries: 0,
    reporter:
      jsonReport === undefined
        ? [['list']]
        : [['list'], ['json', { outputFile: path.resolve(APP, 'e2e', jsonReport) }]],
    ...options,
    use: { baseURL: `http://127.0.0.1:${String(port)}`, trace: 'off' },
    projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
    webServer: {
      cwd: APP.split(path.sep).join('/') + '/',
      command: buildFirst
        ? `${E2E_NODE} node_modules/vite/bin/vite.js build --outDir ${buildDir} && ${server}`
        : server,
      url: `http://127.0.0.1:${String(port)}/ui/app/`,
      reuseExistingServer: false,
      timeout: webServerTimeout,
    },
    // Preserve the original artifact directories when configs move into tests/e2e/configs.
    outputDir: path.resolve(APP, 'e2e', outputDir),
  });
}
