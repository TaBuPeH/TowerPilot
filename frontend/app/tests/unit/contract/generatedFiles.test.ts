// @vitest-environment node
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, expect, it } from 'vitest';
import {
  familyIdentity,
  GENERATED_DIR,
  generatedFile,
  loadContractInputs,
  writeGenerated,
} from '../../../scripts/generate-contract.mjs';
import { HISTORY_DESCRIPTOR } from '../../../scripts/generate-history-contract.mjs';
import { HISTORY_FIXTURE_PATH } from '../../../scripts/history-fixture.mjs';

const folders: string[] = [];
afterEach(() => {
  for (const folder of folders.splice(0)) rmSync(folder, { recursive: true, force: true });
});

/** A generated folder holding one current module, one superseded module and a subfolder. */
function scratch() {
  const dir = mkdtempSync(path.join(tmpdir(), 'tp-generated-'));
  folders.push(dir);
  const current = path.join(dir, 'family_v1.0123abcd.ts');
  const stale = path.join(dir, 'family_v1.89abcdef.ts');
  writeFileSync(current, 'current\n');
  writeFileSync(stale, 'superseded\n');
  mkdirSync(path.join(dir, 'history'));
  writeFileSync(path.join(dir, 'history', 'other_v1.fedcba98.ts'), 'other generator\n');
  return { dir, current, stale, outputs: [[current, 'current\n']] as const };
}

it('check reports a module that no current input produces and leaves it in place', () => {
  const { dir, stale, outputs } = scratch();
  expect(() => {
    writeGenerated(dir, outputs, true, 'regen');
  }).toThrow(/Stale generated modules .* family_v1\.89abcdef\.ts; run regen/);
  expect(existsSync(stale)).toBe(true);
});

it('generation removes a superseded module and keeps current outputs and subfolders', () => {
  const { dir, current, stale, outputs } = scratch();
  writeGenerated(dir, outputs, false, 'regen');
  expect(existsSync(stale)).toBe(false);
  expect(existsSync(current)).toBe(true);
  expect(readdirSync(path.join(dir, 'history'))).toEqual(['other_v1.fedcba98.ts']);
  expect(() => {
    writeGenerated(dir, outputs, true, 'regen');
  }).not.toThrow();
});

// Loading the inputs parses the pages fixture once per descriptor, which takes several seconds.
it('the committed generated folders hold exactly the modules the current inputs produce', () => {
  const hashed = (dir: string) =>
    readdirSync(dir)
      .filter((name) => /\.[a-f\d]{8}\.ts$/.test(name))
      .sort();
  const current = loadContractInputs()
    .map(({ identity, descriptor }) => path.basename(generatedFile(identity, descriptor)))
    .sort();
  expect(hashed(GENERATED_DIR)).toEqual(current);
  const history = generatedFile(
    familyIdentity(HISTORY_FIXTURE_PATH, HISTORY_DESCRIPTOR),
    HISTORY_DESCRIPTOR,
  );
  expect(hashed(path.join(GENERATED_DIR, 'history'))).toEqual([path.basename(history)]);
}, 60_000);
