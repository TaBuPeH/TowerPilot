/**
 * Playwright's API requests open sockets in its Node workers. Windows Node
 * 24.15.0 has silently aborted real-network tests with 0xC0000409; use the
 * Node 22 major used by CI for the runner, workers and fake backend.
 *
 * Keep this check in every config, including direct `playwright test` calls.
 */
if (process.platform === 'win32' && Number(process.versions.node.split('.')[0]) !== 22) {
  throw new Error(
    `Windows Playwright e2e requires Node.js 22; running ${process.version}. ` +
      'Run with Node 22, for example: npx --yes --package=node@22 -- node ' +
      'node_modules/@playwright/test/cli.js test (append the Playwright arguments). ' +
      'Node 24.15.0 can terminate the runner or a worker natively (0xC0000409).',
  );
}

/** Start the fake backend with the same runtime as the runner and its workers. */
export const E2E_NODE = `"${process.execPath}"`;
