// @vitest-environment node
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  loadFixture,
  loadFixtureWithIdentity,
  MissingContractFixture,
} from '../../../scripts/contract-fixture.mjs';
import {
  fixtureFamilies,
  generatedFile,
  generateModule,
  loadContractInputs,
  record,
} from '../../../scripts/generate-contract.mjs';
import { contractForFixture } from '../../../src/contract/generated/index.ts';
import { harvestStatusSchema } from '../../../src/contract/harvestStatus.ts';

const { fixture } = loadFixtureWithIdentity();
const inputs = loadContractInputs();
const families = inputs.flatMap(({ fixture, identity, descriptor }) =>
  fixtureFamilies(fixture, descriptor).map((family) => ({
    ...family,
    schemas: contractForFixture(identity).familySchemas,
  })),
);

/** Add one unknown field at the deepest first object path, including array rows. */
function addNestedField(value: unknown): unknown {
  if (Array.isArray(value))
    return value.map((item: unknown, i) => (i === 0 ? addNestedField(item) : item));
  const object = record(value);
  const plain = (member: unknown): boolean =>
    member !== null && typeof member === 'object' && !Array.isArray(member);
  // Recurse only into a plain object, or an array whose first row is one (an array of
  // strings or numbers has no field to add); otherwise add the field at this level.
  const nested = Object.entries(object).find(
    ([, member]) => plain(member) || (Array.isArray(member) && plain(member[0])),
  );
  return nested === undefined
    ? { ...object, unexpected_contract_field: true }
    : { ...object, [nested[0]]: addNestedField(nested[1]) };
}

describe('loaded strict contract identity', () => {
  // Generating and formatting the Dashboard module (spec 10.3, about 1.4 MB of schemas)
  // takes several seconds, so each input is its own test with a generous timeout.
  it.each(inputs.map((input) => [input.descriptor.name, input] as const))(
    '%s has a committed module and matches regeneration byte for byte',
    async (_name, { fixture, identity, descriptor }) => {
      const file = generatedFile(identity, descriptor);
      expect(existsSync(file)).toBe(true);
      expect(await generateModule(fixture, identity, descriptor)).toBe(readFileSync(file, 'utf8'));
      expect(
        contractForFixture(identity).fixtureVersionSchema.parse(record(fixture)['version']),
      ).toBe(record(fixture)['version']);
    },
    60_000,
  );
  it('refuses an unknown identity with its path, hash and generation command', () => {
    expect(() => contractForFixture({ path: '/new/fixture.json', sha256: 'unknown-hash' })).toThrow(
      '/new/fixture.json (unknown-hash); run npm run contract:generate',
    );
  });
  it('keeps the compatibility entry point strict', () => {
    for (const sample of Object.values(fixture.status))
      expect(harvestStatusSchema.safeParse(sample).success).toBe(true);
  });
  it('fails loudly for an explicit missing file', () => {
    const file = path.resolve('no-such-dir/harvest_status_v1.json');
    expect(() => loadFixture(file)).toThrow(MissingContractFixture);
    expect(() => loadFixture(file)).toThrow(/committed together with frontend\/app/);
  });
});

for (const family of families) {
  describe(family.key, () => {
    const schema = family.schemas[family.key];
    if (schema === undefined) throw new Error(`Missing schema for ${family.key}`);
    it.each(family.samples)('strict-parses $name', ({ value }) => {
      expect(schema.safeParse(value).error?.issues ?? []).toEqual([]);
    });
    it('refuses unknown nested fields, missing required fields and wrong types', () => {
      const first = family.samples[0];
      if (first === undefined) throw new Error('No sample');
      const sample = record(first.value);
      // A family may contain both object and array bodies (profile_reads includes
      // profile sources and profiles lists). The envelope's status is always numeric.
      const key = (Object.hasOwn(sample, 'status') ? ['status'] : Object.keys(sample)).find((key) =>
        family.samples.every(({ value }) => Object.hasOwn(record(value), key)),
      );
      if (key === undefined) throw new Error('No required field');
      expect(schema.safeParse(addNestedField(sample)).success).toBe(false);
      expect(
        schema.safeParse(
          Object.fromEntries(Object.entries(sample).filter(([name]) => name !== key)),
        ).success,
      ).toBe(false);
      expect(schema.safeParse({ ...sample, [key]: [] }).success).toBe(false);
    });
  });
}
