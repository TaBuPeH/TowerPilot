// @vitest-environment node
import { expect, it } from 'vitest';
import { loadFixtureWithIdentity } from '../../../scripts/contract-fixture.mjs';
import { fixtureFamilies } from '../../../scripts/generate-contract.mjs';
import { contractForFixture } from '../../../src/contract/generated/index.ts';
import { requireR6Fixture } from './requireR6.ts';

requireR6Fixture();
const { fixture, identity } = loadFixtureWithIdentity();
const contract = contractForFixture(identity);
for (const family of fixtureFamilies(fixture).filter(
  ({ key }) => key.startsWith('stop_operation.') || key === 'refusals',
)) {
  const schema = contract.familySchemas[family.key];
  if (schema === undefined) throw new Error(`Missing r6 schema: ${family.key}`);
  it.each(family.samples)(`${family.key}: $name`, ({ value }) => {
    expect(schema.safeParse(value).error?.issues ?? []).toEqual([]);
  });
}
