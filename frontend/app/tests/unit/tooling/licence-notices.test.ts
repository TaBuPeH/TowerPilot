// @vitest-environment node
import { APP } from '../../support/paths.ts';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { build, type Plugin } from 'vite';
import { afterEach, describe, expect, it } from 'vitest';
import {
  classifyModule,
  formatNotices,
  NOTICES_FILE,
  packageDirOf,
  readPackage,
} from '../../../scripts/licence-notices.ts';

const temporary: string[] = [];

afterEach(() => {
  for (const dir of temporary.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function fakePackage(files: Record<string, string>): string {
  const dir = mkdtempSync(path.join(tmpdir(), 'tp-notices-'));
  temporary.push(dir);
  mkdirSync(dir, { recursive: true });
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(path.join(dir, name), content);
  }
  return dir;
}

describe('packageDirOf', () => {
  it.each([
    [
      'D:\\checkout\\frontend\\app\\node_modules\\react\\cjs\\react.production.js',
      'D:/checkout/frontend/app/node_modules/react',
    ],
    ['/repo/node_modules/@scope/pkg/dist/index.js', '/repo/node_modules/@scope/pkg'],
    [
      '/repo/node_modules/a/node_modules/b/index.js?commonjs-exports',
      '/repo/node_modules/a/node_modules/b',
    ],
    ['\0/repo/node_modules/zod/index.js', '/repo/node_modules/zod'],
  ])('finds the package of %s', (id, expected) => {
    expect(packageDirOf(id)).toBe(expected);
  });

  it.each(['/repo/frontend/app/src/app/App.tsx', '\0vite/preload-helper', '/repo/node_modules/'])(
    'ignores %s',
    (id) => {
      expect(packageDirOf(id)).toBeNull();
    },
  );
});

describe('readPackage and formatNotices', () => {
  it('copies the full licence text of each bundled package', () => {
    const dir = fakePackage({
      'package.json': JSON.stringify({ name: 'demo', version: '1.2.3', license: 'MIT' }),
      LICENSE: 'MIT License\n\nCopyright (c) Demo',
    });
    const pkg = readPackage(dir);
    expect(pkg).toEqual({
      name: 'demo',
      version: '1.2.3',
      license: 'MIT',
      licenceText: 'MIT License\n\nCopyright (c) Demo',
    });
    const text = formatNotices([pkg]);
    expect(text).toContain('- demo 1.2.3 (MIT)');
    expect(text).toContain('Copyright (c) Demo');
  });

  it('fails when a bundled package has no licence file', () => {
    const dir = fakePackage({ 'package.json': JSON.stringify({ name: 'bare', version: '0.0.1' }) });
    expect(() => readPackage(dir)).toThrow(/ships no licence file/);
  });
});

describe('classifyModule', () => {
  it.each([
    ['\0vite/modulepreload-polyfill.js', 'vite'],
    ['\0rolldown/runtime.js', 'rolldown'],
    [path.join(APP, 'node_modules', 'react', 'index.js'), 'react'],
  ])('gives %j to the %s package', (id, name) => {
    const owner = classifyModule(id, APP);
    expect(owner.kind).toBe('package');
    expect(owner.kind === 'package' && path.basename(owner.dir)).toBe(name);
  });

  it.each([
    path.join(APP, 'src', 'app', 'App.tsx'),
    `${path.join(APP, 'index.html')}?html-proxy&index=0.js`,
  ])('treats %s as app code', (id) => {
    expect(classifyModule(id, APP)).toEqual({ kind: 'app' });
  });

  it.each([
    '\0no-such-helper/x.js',
    '\0../escape.js',
    path.join(path.dirname(APP), 'elsewhere.js'),
  ])('does not know %j, so the build would fail', (id) => {
    expect(classifyModule(id, APP)).toEqual({ kind: 'unknown' });
  });
});

describe('the real build (R3-08)', () => {
  it('lists the owner of every module it renders, injected helpers included', async () => {
    const cache = path.join(APP, 'node_modules', '.cache');
    mkdirSync(cache, { recursive: true });
    const outDir = mkdtempSync(path.join(cache, 'tp-notices-build-'));
    temporary.push(outDir);
    const rendered: string[] = [];
    const observer: Plugin = {
      name: 'notices-observer',
      enforce: 'post',
      generateBundle(_options, bundle) {
        for (const output of Object.values(bundle)) {
          if (output.type === 'chunk') {
            for (const [id, module] of Object.entries(output.modules)) {
              if (module.renderedLength > 0) rendered.push(id);
            }
          }
        }
      },
    };
    await build({
      root: APP,
      logLevel: 'silent',
      build: { outDir, emptyOutDir: true },
      plugins: [observer],
    });
    const notices = readFileSync(path.join(outDir, NOTICES_FILE), 'utf8');
    const owners = new Set<string>();
    for (const id of rendered) {
      const owner = classifyModule(id, APP);
      expect(owner.kind, id).not.toBe('unknown');
      if (owner.kind === 'package') owners.add(readPackage(owner.dir).name);
    }
    expect(rendered).toEqual(
      expect.arrayContaining(['\0vite/modulepreload-polyfill.js', '\0rolldown/runtime.js']),
    );
    // The Dashboard parses every answer with zod (api/wire.ts), so zod ships too (F2).
    expect([...owners].sort()).toEqual([
      'react',
      'react-dom',
      'rolldown',
      'scheduler',
      'vite',
      'zod',
    ]);
    for (const name of owners) {
      const pkg = readPackage(path.join(APP, 'node_modules', name));
      expect(notices).toContain(`- ${pkg.name} ${pkg.version} (${pkg.license})`);
      expect(notices).toContain(pkg.licenceText);
    }
  }, 60_000);
});
