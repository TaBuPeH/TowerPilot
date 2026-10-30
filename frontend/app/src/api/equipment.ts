import {
  contextFromConfig,
  contextKey,
  expectedContext,
  sameContext,
  type PageContext,
} from './context.ts';
import { createClickTimeRevisionContextRead } from './clickContext.ts';
import { readEndpoint } from './client.ts';
import { createIntentMachine } from './intents/machine.ts';
import { sendOnce } from './intents/send.ts';
import { createSequence } from './sequence.ts';
import {
  browserSessionStorage,
  keysWithPrefix,
  readJsonItem,
  writeJsonItem,
  type KeyValueStorage,
} from './storage.ts';
import { fetchTransport, type Transport } from './transport.ts';
import { bodyMembers, ENDPOINTS, type Endpoint } from './wire.ts';
import { contextFenceAnswerSchema } from '../contract/contextFence.ts';
import {
  configFieldsMore2ConfigAnswerSchema,
  configFieldsMore2LoadoutPatchAnswerSchema,
  configFieldsMore2SourceAnswerSchema,
  restoreConfigAnswerSchema,
  restoreEligibilityConfigAnswerSchema,
  restoreEligibilityPatchAnswerSchema,
  restorePatchAnswerSchema,
} from '../contract/configFields.ts';
import {
  equipmentConfigAnswerSchema,
  equipmentContextAnswerSchema,
  equipmentPatchAnswerSchema,
  equipmentProfileAnswerSchema,
  equipmentDiscoveryAnswerSchema,
  equipmentSampleConfigAnswerSchema,
  equipmentSampleProfileAnswerSchema,
  equipmentSamplePatchAnswerSchema,
} from '../contract/equipment.ts';

const endpoint = (schemas: Endpoint['strict'], method: 'GET' | 'POST' = 'GET'): Endpoint => ({
  id: method === 'GET' ? 'config' : 'runControlsSave',
  method,
  strict: schemas,
  members: bodyMembers(schemas),
});
const config = endpoint([
  equipmentConfigAnswerSchema,
  equipmentContextAnswerSchema,
  equipmentSampleConfigAnswerSchema,
  restoreConfigAnswerSchema,
  restoreEligibilityConfigAnswerSchema,
  // r12f: the config after the restore-only save and after the refusals.
  configFieldsMore2ConfigAnswerSchema,
]);
const profile = endpoint([
  equipmentProfileAnswerSchema,
  equipmentDiscoveryAnswerSchema,
  equipmentSampleProfileAnswerSchema,
  // r12f: a profile with no usable blueprints mapping (absent, null, a list or {}).
  configFieldsMore2SourceAnswerSchema,
]);
const patch = endpoint(
  [
    equipmentPatchAnswerSchema,
    equipmentSamplePatchAnswerSchema,
    contextFenceAnswerSchema,
    restorePatchAnswerSchema,
    restoreEligibilityPatchAnswerSchema,
    // r12f: restore-only save and the refusals for a profile without blueprints.
    configFieldsMore2LoadoutPatchAnswerSchema,
  ],
  'POST',
);

function intentStorage(storage: KeyValueStorage | null): KeyValueStorage | null {
  if (storage === null) return null;
  const prefix = 'tp.equipment:';
  const keys = () => keysWithPrefix(storage, prefix);
  return {
    get length() {
      return keys().length;
    },
    key: (index) => keys()[index]?.slice(prefix.length) ?? null,
    getItem: (key) => storage.getItem(prefix + key),
    setItem: (key, value) => {
      storage.setItem(prefix + key, value);
    },
    removeItem: (key) => {
      storage.removeItem(prefix + key);
    },
  };
}

/** Equipment owns its transport adapter; the shared machine journals each explicit save once.
 * `run_save` is the existing non-resendable saved-configuration journal kind. Its Runs-specific
 * success classifier deliberately cannot confirm a loadout answer: Equipment checks the named
 * body and configuration revision locally, then settles the journal with a config read.
 * Strict fixture drift alone cannot reject an arbitrary user-owned loadout name.
 * Neither the wire body nor the backend answer is translated into a fabricated Runs answer.
 */
export function createEquipmentApi(
  transport: Transport = fetchTransport,
  storage: KeyValueStorage | null = browserSessionStorage(),
  windowMs?: number,
) {
  const sequence = createSequence();
  const machine = createIntentMachine({
    transport,
    storage: intentStorage(storage),
    sequence,
    endpoints: { ...ENDPOINTS, runControlsSave: patch },
    ...(windowMs === undefined ? {} : { windowMs }),
  });
  return {
    machine,
    sequence,
    targetKey: contextKey,
    sameContext,
    readClickContext: createClickTimeRevisionContextRead(transport, config),
    configMatches: (body: unknown, context: PageContext) =>
      sameContext(contextFromConfig(body), context),
    readConfig: (signal: AbortSignal) => readEndpoint(transport, config, '/api/config', signal),
    readProfile: (name: string, signal: AbortSignal) =>
      readEndpoint(transport, profile, `/api/profile-src/${encodeURIComponent(name)}`, signal),
    send: (
      context: PageContext,
      name: string,
      revision: string,
      body: Readonly<Record<string, unknown>>,
    ) => {
      const target = `equipment:${contextKey(context)}:${name}`;
      return sendOnce(machine, target, context, () =>
        machine.send({
          kind: 'run_save',
          target,
          endpoint: 'runControlsSave',
          path: '/api/loadout-patch',
          body: {
            name,
            body,
            expected_revision: revision,
            expected_context: expectedContext(context),
          },
          context,
          key: null,
          meta: { blueprint: name, profileRevision: revision },
          supersedes: [],
        }),
      );
    },
    loadDraft: (key: string) => readJsonItem(storage, `tp.equipment.draft:${key}`),
    keepDraft: (key: string, draft: unknown) =>
      writeJsonItem(storage, `tp.equipment.draft:${key}`, draft),
  };
}
export type EquipmentApi = ReturnType<typeof createEquipmentApi>;
