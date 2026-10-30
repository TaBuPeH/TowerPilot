// Runs the Playwright suite under Node 22 on Windows (P2: Node 24.15 aborts
// workers natively with 0xC0000409). Elsewhere, or when already on Node 22,
// it runs Playwright with the current Node. Arguments pass through.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const cli = fileURLToPath(new URL('../node_modules/@playwright/test/cli.js', import.meta.url));
const args = ['test', ...process.argv.slice(2)];
const onNode22 = process.versions.node.split('.')[0] === '22';
const result =
  process.platform === 'win32' && !onNode22
    ? spawnSync('npx', ['--yes', '--package=node@22', '--', 'node', cli, ...args], {
        stdio: 'inherit',
        shell: true,
      })
    : spawnSync(process.execPath, [cli, ...args], { stdio: 'inherit' });
process.exit(result.status ?? 1);
