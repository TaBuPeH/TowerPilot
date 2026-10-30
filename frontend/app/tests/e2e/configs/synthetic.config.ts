import { isolatedConfig } from './isolated-config.ts';

const port = Number(process.env['TP_AC7_PORT']);
const build = process.env['TP_AC7_BUILD'];
if (!Number.isSafeInteger(port) || port <= 0 || [4174, 8620, 8630].includes(port) || !build)
  throw new Error(
    'AC7 needs TP_AC7_PORT (a free non-default port) and TP_AC7_BUILD (isolated build)',
  );

/**
 * The shared factory supplies the ordinary browser settings, retry count, worker count and
 * timeout. The isolated build can be any path, so the factory's `--build-dir` argument gets it
 * quoted for the shell here; no other config passes an arbitrary path.
 */
export default isolatedConfig({
  port,
  testMatch: 'synthetic-actions.spec.ts',
  buildDir: `"${build}"`,
  outputDir: '../test-results/synthetic-e2e',
});
