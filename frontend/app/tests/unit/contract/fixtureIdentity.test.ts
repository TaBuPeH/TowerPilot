// @vitest-environment node
import { createHash } from 'node:crypto';
import { readFileSync, statSync, existsSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FIXTURE_PATH,
  FIXTURE_PATH,
  fixtureIdentity,
  loadFixtureWithIdentity,
  resolveFixturePath,
} from '../../../scripts/contract-fixture.mjs';

describe('file-only fixture override', () => {
  it('prints and hashes exactly the bytes parsed by the loader', () => {
    const { identity } = loadFixtureWithIdentity();
    console.log(`Contract fixture: ${identity.path}\nSHA-256: ${identity.sha256}`);
    expect(identity.path).toBe(FIXTURE_PATH);
    expect(identity).toEqual(fixtureIdentity());
    expect(identity.sha256).toBe(
      createHash('sha256').update(readFileSync(identity.path)).digest('hex'),
    );
  });
  it('uses the default only for an unset override', () => {
    expect(resolveFixturePath(undefined, () => false)).toBe(DEFAULT_FIXTURE_PATH);
  });
  it.each([
    'https://example.test/f.json',
    'http://localhost/f.json',
    'file:///E:/f.json',
    'data:application/json,{}',
    'ftp:fixture',
  ])('refuses URL scheme %s even if the filesystem predicate accepts it', (value) => {
    expect(() => resolveFixturePath(value, () => true)).toThrow(
      `TP_CONTRACT_FIXTURE must name an existing regular file, got ${value}`,
    );
  });
  it.each(['', 'missing.json', 'directory'])('refuses a non-file %s with no fallback', (value) => {
    expect(() => resolveFixturePath(value, () => false)).toThrow(
      `TP_CONTRACT_FIXTURE must name an existing regular file, got ${value}`,
    );
  });
  it('accepts a Windows drive-letter path unchanged', () => {
    const value = 'E:\\fixtures\\contract.json';
    expect(resolveFixturePath(value, (file) => file === value)).toBe(value);
    expect(resolveFixturePath('E:/fixtures/contract.json', () => true)).toBe(
      'E:/fixtures/contract.json',
    );
  });
  it('validates real files, directories and missing paths using filesystem observations', () => {
    const regular = (file: string) => existsSync(file) && statSync(file).isFile();
    expect(resolveFixturePath(FIXTURE_PATH, regular)).toBe(FIXTURE_PATH);
    expect(() => resolveFixturePath('.', regular)).toThrow('TP_CONTRACT_FIXTURE');
    expect(() => resolveFixturePath('./no-such-fixture.json', regular)).toThrow(
      './no-such-fixture.json',
    );
  });
});
