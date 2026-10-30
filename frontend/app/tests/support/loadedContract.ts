import { z } from 'zod';
import {
  DASHBOARD_FIXTURE_PATH,
  fixtureIdentity,
  loadJsonWithIdentity,
} from '../../scripts/contract-fixture.mjs';
import {
  createEndpoints,
  DEFAULT_CONTRACT,
  type ContractSchemas,
  type Endpoint,
  type EndpointId,
} from '../../src/api/wire.ts';
import { contractForFixture } from '../../src/contract/generated/index.ts';

/**
 * The contract of the fixtures this run loaded (the checkout's r6 fixture, or
 * another one named by TP_CONTRACT_FIXTURE, spec 10.4) and the endpoint table
 * built from it. For the checkout fixture this is the production contract
 * (DEFAULT_CONTRACT, B-04).
 */
export const harvestModule = contractForFixture(fixtureIdentity());
export const dashboardModule = contractForFixture(
  loadJsonWithIdentity(DASHBOARD_FIXTURE_PATH).identity,
);

export function schemaOf(module: Readonly<Record<string, unknown>>, name: string): z.ZodType {
  const found = module[name];
  if (!(found instanceof z.ZodType)) throw new Error(`no schema ${name}`);
  return found;
}

export function objectSchema(module: Readonly<Record<string, unknown>>, name: string): z.ZodObject {
  const found = module[name];
  if (!(found instanceof z.ZodObject)) throw new Error(`no object schema ${name}`);
  return found;
}

export const LOADED_CONTRACT: ContractSchemas = {
  ...DEFAULT_CONTRACT,
  harvestStatus: schemaOf(harvestModule, 'harvestStatusSchema'),
  postHarvest: schemaOf(harvestModule, 'postHarvestAnswerSchema'),
  automationAccess: schemaOf(harvestModule, 'automationAccessAnswerSchema'),
  refusal: schemaOf(harvestModule, 'refusalAnswerSchema'),
  startTransfer: schemaOf(harvestModule, 'startTransferAnswerSchema'),
  stopCancel: schemaOf(harvestModule, 'stopCancelAnswerSchema'),
  stopConflict: schemaOf(harvestModule, 'stopConflictAnswerSchema'),
  stopKill: schemaOf(harvestModule, 'stopKillAnswerSchema'),
  statusBody: objectSchema(objectSchema(dashboardModule, 'statusAnswerSchema').shape, 'body'),
};

export const LOADED_ENDPOINTS: Readonly<Record<EndpointId, Endpoint>> =
  createEndpoints(LOADED_CONTRACT);
