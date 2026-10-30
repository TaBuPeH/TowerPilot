// @vitest-environment node
import { APP } from '../../support/paths.ts';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { build, type Rolldown } from 'vite';
import { afterEach, describe, expect, it } from 'vitest';
import { graphProblems, INVENTORY_FILE, inventoryOf } from '../../../scripts/build-inventory.ts';

const temporary: string[] = [];

afterEach(() => {
  for (const dir of temporary.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function scratch(prefix: string): string {
  const cache = path.join(APP, 'node_modules', '.cache');
  mkdirSync(cache, { recursive: true });
  const dir = mkdtempSync(path.join(cache, prefix));
  temporary.push(dir);
  return dir;
}

function chunk(
  fileName: string,
  imports: string[],
  importedCss: string[] = [],
): Rolldown.OutputChunk {
  const value: unknown = {
    type: 'chunk',
    fileName,
    imports,
    dynamicImports: [],
    viteMetadata: { importedCss: new Set(importedCss), importedAssets: new Set<string>() },
  };
  // A partial chunk is enough for graphProblems; only these fields are read.
  return value as Rolldown.OutputChunk;
}

describe('graphProblems', () => {
  it('reports a chunk import and a stylesheet the bundle does not contain', () => {
    const entry = chunk('assets/index.js', ['assets/vendor.js'], ['assets/index.css']);
    expect(graphProblems({ 'assets/index.js': entry })).toEqual([
      'assets/index.js needs assets/vendor.js, which the build does not contain',
      'assets/index.js needs assets/index.css, which the build does not contain',
    ]);
    const vendor = chunk('assets/vendor.js', []);
    const css: unknown = { type: 'asset', fileName: 'assets/index.css' };
    expect(
      graphProblems({
        'assets/index.js': entry,
        'assets/vendor.js': vendor,
        'assets/index.css': css as Rolldown.OutputAsset,
      }),
    ).toEqual([]);
  });
});

describe('inventoryOf', () => {
  it('lists every file with its SHA-256, but not itself', () => {
    const dir = scratch('tp-inventory-');
    mkdirSync(path.join(dir, 'assets'));
    writeFileSync(path.join(dir, 'index.html'), 'html');
    writeFileSync(path.join(dir, 'assets', 'a.js'), 'js');
    writeFileSync(path.join(dir, INVENTORY_FILE), 'old');
    const lines = inventoryOf(dir)
      .split('\n')
      .filter((line) => line !== '' && !line.startsWith('#'));
    const sha = (text: string) => createHash('sha256').update(text).digest('hex');
    expect(lines).toEqual([`${sha('js')}  assets/a.js`, `${sha('html')}  index.html`]);
  });
});

describe('the real build (compliance M02)', () => {
  it('writes an inventory naming exactly its files with their hashes', async () => {
    const outDir = scratch('tp-inventory-build-');
    await build({ root: APP, logLevel: 'silent', build: { outDir, emptyOutDir: true } });
    const listed = new Map(
      readFileSync(path.join(outDir, INVENTORY_FILE), 'utf8')
        .split('\n')
        .filter((line) => line !== '' && !line.startsWith('#'))
        .map((line) => [line.slice(66), line.slice(0, 64)] as const),
    );
    const files = readdirSync(outDir, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile())
      .map((entry) =>
        path.relative(outDir, path.join(entry.parentPath, entry.name)).replaceAll('\\', '/'),
      )
      .filter((name) => name !== INVENTORY_FILE)
      .sort();
    expect([...listed.keys()].sort()).toEqual(files);
    for (const name of files) {
      expect(listed.get(name)).toBe(
        createHash('sha256')
          .update(readFileSync(path.join(outDir, name)))
          .digest('hex'),
      );
    }
  }, 60_000);
});
