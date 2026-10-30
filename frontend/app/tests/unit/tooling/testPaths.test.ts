// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vitest';
import { APP, REPO, SRC } from '../../support/paths.ts';

it('anchors test roots to the app package, product source and repository', () => {
  expect(JSON.parse(readFileSync(path.join(APP, 'package.json'), 'utf8'))).toHaveProperty(
    'name',
    'tower-pilot-app',
  );
  expect(SRC).toBe(path.join(APP, 'src'));
  expect(existsSync(path.join(SRC, 'core/actions.ts'))).toBe(true);
  expect(APP).toBe(path.join(REPO, 'frontend/app'));
  expect(existsSync(path.join(REPO, 'CLAUDE.md'))).toBe(true);
});
