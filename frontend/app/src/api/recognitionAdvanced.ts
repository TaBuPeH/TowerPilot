import { contextKey, sameContext, type PageContext } from './context.ts';
import { createClickTimeLaunchContextRead, type LaunchContext } from './clickContext.ts';
import { readEndpoint } from './client.ts';
import { createIntentMachine } from './intents/machine.ts';
import { createSequence } from './sequence.ts';
import {
  browserSessionStorage,
  keysWithPrefix,
  readJsonItem,
  writeJsonItem,
  type KeyValueStorage,
} from './storage.ts';
import { fetchTransport, type ApiPath, type Transport } from './transport.ts';
import { randomId } from './timers.ts';
import { bodyMembers, ENDPOINTS, type Endpoint } from './wire.ts';
import * as schemas from '../contract/recognitionAdvanced.ts';
import type { OperationView } from '../core/recognition.ts';
import type { Rect } from '../core/recognitionImages.ts';

const endpoint = (strict: Endpoint['strict']): Endpoint => ({
  id: 'config',
  method: 'GET',
  strict,
  members: bodyMembers(strict),
});
const reads = {
  templates: endpoint([
    schemas.advancedTemplatesAnswerSchema,
    schemas.advancedTemplatesReadbackAnswerSchema,
  ]),
  required: endpoint([schemas.advancedRequiredAnswerSchema]),
  catalogue: endpoint([schemas.advancedCatalogueAnswerSchema]),
  plan: endpoint([schemas.advancedPlanAnswerSchema, schemas.advancedPlanReadbackAnswerSchema]),
  analysis: endpoint([schemas.analysisStatusAnswerSchema]),
};
function journal(storage: KeyValueStorage | null): KeyValueStorage | null {
  if (storage === null) return null;
  const prefix = 'tp.recognition.advanced:';
  const keys = () => keysWithPrefix(storage, prefix);
  return {
    get length() {
      return keys().length;
    },
    key: (i) => keys()[i]?.slice(prefix.length) ?? null,
    getItem: (k) => storage.getItem(prefix + k),
    setItem: (k, v) => {
      storage.setItem(prefix + k, v);
    },
    removeItem: (k) => {
      storage.removeItem(prefix + k);
    },
  };
}
/** One isolated journal, no resend API. Images are loaded by explicit token-bound img elements. */
export function createRecognitionAdvancedApi(
  transport: Transport = fetchTransport,
  storage: KeyValueStorage | null = browserSessionStorage(),
  windowMs?: number,
) {
  const sequence = createSequence();
  const machine = createIntentMachine({
    transport,
    storage: journal(storage),
    sequence,
    ...(windowMs === undefined ? {} : { windowMs }),
  });
  return {
    machine,
    sequence,
    targetKey: contextKey,
    sameContext,
    operationMatches: (operation: OperationView, context: PageContext) => {
      const { instance, serial, account, profile } = operation.context;
      return (
        instance !== null &&
        serial !== null &&
        sameContext({ instance, serial, account, profile }, context)
      );
    },
    newToken: randomId,
    readClickContext: createClickTimeLaunchContextRead(transport, ENDPOINTS.config),
    read: (kind: keyof typeof reads, path: ApiPath, signal: AbortSignal) =>
      readEndpoint(transport, reads[kind], path, signal),
    launch: (context: LaunchContext) => {
      const key = randomId();
      return machine.send({
        kind: 'analysis_start',
        target: `analysis:${contextKey(context)}`,
        endpoint: 'analysisStart',
        path: '/api/screen-analysis/start',
        body: { expected_context: context, idempotency_key: key },
        context,
        key,
        meta: {},
        supersedes: [],
      });
    },
    saveTemplate: (
      context: PageContext,
      name: string,
      token: string,
      rect: Rect,
      overwrite: boolean,
    ) =>
      machine.send({
        kind: 'template_write',
        target: `crop:${contextKey(context)}`,
        endpoint: 'templateWrite',
        path: `/api/template/${name.split('/').map(encodeURIComponent).join('/')}`,
        body: { ts: token, ...rect, ...(overwrite ? { overwrite: true } : {}) },
        context,
        key: null,
        meta: { name },
        supersedes: [],
      }),
    loadDraft: (key: string) => readJsonItem(storage, `tp.recognition.crop:${key}`),
    keepDraft: (key: string, draft: unknown) =>
      writeJsonItem(storage, `tp.recognition.crop:${key}`, draft),
  };
}
export type RecognitionAdvancedApi = ReturnType<typeof createRecognitionAdvancedApi>;
