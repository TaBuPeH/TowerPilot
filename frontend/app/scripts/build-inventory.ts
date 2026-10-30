import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Plugin, Rolldown } from 'vite';

/**
 * Writes BUILD_INVENTORY.txt into the build folder after Vite has written it:
 * one `<sha256>  <path>` line per file of the completed build. Release
 * packaging (tools/build_release.py) refuses a build whose files do not match
 * it, so a build missing a chunk, stylesheet or asset cannot ship.
 *
 * Before writing, the bundle's own graph is checked: every chunk's static and
 * dynamic imports and its imported CSS and assets must be outputs of this build.
 */
export const INVENTORY_FILE = 'BUILD_INVENTORY.txt';

/** Problems in the bundle graph: references to files the bundle does not contain. */
export function graphProblems(bundle: Rolldown.OutputBundle): string[] {
  const problems: string[] = [];
  for (const output of Object.values(bundle)) {
    if (output.type !== 'chunk') {
      continue;
    }
    const metadata = output.viteMetadata;
    const references = [
      ...output.imports,
      ...output.dynamicImports,
      ...(metadata === undefined ? [] : [...metadata.importedCss, ...metadata.importedAssets]),
    ];
    for (const reference of references) {
      if (!(reference in bundle)) {
        problems.push(`${output.fileName} needs ${reference}, which the build does not contain`);
      }
    }
  }
  return problems;
}

function files(root: string, folder = ''): string[] {
  return readdirSync(path.join(root, folder), { withFileTypes: true }).flatMap((entry) => {
    const relative = folder === '' ? entry.name : `${folder}/${entry.name}`;
    return entry.isDirectory() ? files(root, relative) : [relative];
  });
}

/** The inventory text for the files now in `outDir` (the inventory itself excluded). */
export function inventoryOf(outDir: string): string {
  const lines = files(outDir)
    .filter((name) => name !== INVENTORY_FILE)
    .sort()
    .map(
      (name) =>
        `${createHash('sha256')
          .update(readFileSync(path.join(outDir, name)))
          .digest('hex')}  ${name}`,
    );
  return [
    '# Files of this dashboard app build: <sha256>  <path relative to frontend/webui/app>.',
    '# Written by frontend/app/scripts/build-inventory.ts; checked by tools/build_release.py.',
    ...lines,
    '',
  ].join('\n');
}

export function buildInventory(): Plugin {
  return {
    name: 'tower-pilot-build-inventory',
    apply: 'build',
    enforce: 'post',
    writeBundle(options, bundle) {
      const problems = graphProblems(bundle);
      if (problems.length > 0) {
        throw new Error(`The build is incomplete:\n${problems.join('\n')}`);
      }
      const outDir = options.dir;
      if (outDir === undefined) {
        throw new Error('The build has no output folder to inventory');
      }
      writeFileSync(path.join(outDir, INVENTORY_FILE), inventoryOf(outDir));
    },
  };
}
