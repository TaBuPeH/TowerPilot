import {
  profilePatchFencedPatchAnswerSchema,
  profilePatchFencedSourceAnswerSchema,
} from '../contract/profilePatchFenced.ts';
import {
  contextFromConfig,
  contextKey,
  expectedContext,
  sameContext,
  type PageContext,
} from './context.ts';
import { createClickTimeContextRead, createClickTimeTargetCheck } from './clickContext.ts';
import { GET_TIMEOUT_MS } from './poller.ts';
import { editTargetScope } from '../core/editingTarget.ts';
import { readEndpoint } from './client.ts';
import { createIntentMachine } from './intents/machine.ts';
import { retainedProfilePatchRefusal } from './intents/persistence.ts';
import { draftSettlement } from './intents/draftSettlement.ts';
import { sendOnce } from './intents/send.ts';
import { createSequence } from './sequence.ts';
import { restoreDrafts, saveDrafts } from './drafts.ts';
import {
  browserSessionStorage,
  prefixedStorage,
  readJsonItem,
  writeJsonItem,
  type KeyValueStorage,
} from './storage.ts';
import { fetchTransport, type ApiPath, type Transport } from './transport.ts';
import { bodyMembers, ENDPOINTS, type Endpoint } from './wire.ts';
import { own } from '../core/read.ts';
import * as schemas from '../contract/abilities.ts';
import {
  profileTargetConfigAnswerSchema,
  profileTargetSourceAnswerSchema,
  profileTargetPatchAnswerSchema,
  profileTargetMoreSourceAnswerSchema,
  profileTargetMorePatchAnswerSchema,
} from '../contract/profileTarget.ts';

const endpoint = (strict: Endpoint['strict'], method: 'GET' | 'POST' = 'GET'): Endpoint => ({
  id: method === 'GET' ? 'config' : 'runControlsSave',
  method,
  strict,
  members: bodyMembers(strict),
});
const config = endpoint([schemas.abilitiesConfigAnswerSchema, profileTargetConfigAnswerSchema]);
const profile = endpoint([
  profileTargetMoreSourceAnswerSchema,
  profilePatchFencedSourceAnswerSchema,
  schemas.abilitiesSourceAnswerSchema,
  schemas.abilitiesProfileAnswerSchema,
  schemas.abilitiesSampleSourceAnswerSchema,
  profileTargetSourceAnswerSchema,
]);
const vocab = endpoint([schemas.abilitiesVocabAnswerSchema]);
const settings = endpoint([schemas.abilitiesSettingsAnswerSchema]);
const patch = endpoint(
  [
    profileTargetMorePatchAnswerSchema,
    schemas.abilitiesPatchAnswerSchema,
    schemas.abilitiesSettingsSaveAnswerSchema,
    schemas.abilitiesSamplePatchAnswerSchema,
    profileTargetPatchAnswerSchema,
    profilePatchFencedPatchAnswerSchema,
  ],
  'POST',
);

const intentTarget = (scope: string, body: unknown) =>
  `abilities:${scope}:${JSON.stringify(own(body, 'path') ?? 'global')}`;

/** Non-resendable, isolated journal. Raw drafts keep frozen submissions; unresolved
 * submissions restore as unknown, while the shared journal retains definite runtime
 * refusals. Policy targets are independent so saving another policy cannot erase one. */
export function createAbilitiesApi(
  transport: Transport = fetchTransport,
  storage: KeyValueStorage | null = browserSessionStorage(),
  windowMs?: number,
) {
  const machine = createIntentMachine({
    transport,
    storage: prefixedStorage(storage, 'tp.abilities.journal:'),
    sequence: createSequence(),
    endpoints: { ...ENDPOINTS, runControlsSave: patch },
    ...(windowMs === undefined ? {} : { windowMs }),
  });
  const submissionRecord = (scope: string, body: unknown) => {
    const name = own(body, 'name');
    if (typeof name !== 'string') return null;
    return (
      machine
        .snapshot()
        .findLast(
          (item) =>
            item.target === intentTarget(scope, body) &&
            item.context !== null &&
            editTargetScope(item.context, name) === scope &&
            item.supersededBySeq === null &&
            item.request.body === JSON.stringify(body),
        ) ?? null
    );
  };
  return {
    machine,
    targetKey: contextKey,
    editTargetScope,
    expectedContext,
    readClickContext: createClickTimeContextRead(transport, config),
    awaitingAnswer: (scope: string, body: unknown) => {
      const item = submissionRecord(scope, body);
      return item !== null && draftSettlement(item) === 'pending';
    },
    profileRefusal: (scope: string, body: unknown) => {
      const item = submissionRecord(scope, body);
      return item !== null && retainedProfilePatchRefusal(item) ? item : null;
    },
    checkTarget: createClickTimeTargetCheck(transport, {
      config,
      profileFiles: ENDPOINTS.profileFiles,
    }),
    configMatches: (value: unknown, context: PageContext) =>
      sameContext(contextFromConfig(value), context),
    config: (signal: AbortSignal) => readEndpoint(transport, config, '/api/config', signal),
    profile: (name: string, signal: AbortSignal) =>
      readEndpoint(transport, profile, `/api/profile-src/${encodeURIComponent(name)}`, signal),
    vocab: (signal: AbortSignal) => readEndpoint(transport, vocab, '/api/vocab', signal),
    settings: (signal: AbortSignal) =>
      readEndpoint(
        transport,
        settings,
        '/api/settings',
        AbortSignal.any([signal, AbortSignal.timeout(GET_TIMEOUT_MS)]),
      ),
    send: (
      context: PageContext,
      path: ApiPath,
      body: Readonly<Record<string, unknown>>,
      scope = contextKey(context),
    ) =>
      sendOnce(machine, intentTarget(scope, body), context, () =>
        machine.send({
          kind: path === '/api/profile-patch' ? 'profile_patch' : 'run_save',
          target: intentTarget(scope, body),
          endpoint: 'runControlsSave',
          path,
          body,
          context,
          key: null,
          meta: {},
          supersedes: [],
        }),
      ),
    load: (key: string) => readJsonItem(storage, `tp.abilities:${key}`),
    keep: (key: string, value: unknown) => writeJsonItem(storage, `tp.abilities:${key}`, value),
    loadNewId: (key: string, family: string): string => {
      const value = restoreDrafts(storage)[`abilities:new_${family}:${key}`]?.['id'];
      return typeof value === 'string' ? value : '';
    },
    keepNewId: (key: string, family: string, value: string) => {
      saveDrafts(storage, {
        ...restoreDrafts(storage),
        [`abilities:new_${family}:${key}`]: value === '' ? {} : { id: value },
      });
    },
  };
}
export type AbilitiesApi = ReturnType<typeof createAbilitiesApi>;
