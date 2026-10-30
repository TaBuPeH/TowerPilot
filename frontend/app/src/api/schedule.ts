import { profilePatchFencedSourceAnswerSchema } from '../contract/profilePatchFenced.ts';
import {
  contextFromConfig,
  contextKey,
  expectedContext,
  readStoredContext,
  sameContext,
  type PageContext,
} from './context.ts';
import { own } from '../core/read.ts';
import { readEndpoint } from './client.ts';
import { createClickTimeContextRead, createClickTimeTargetCheck } from './clickContext.ts';
import { editTargetScope } from '../core/editingTarget.ts';
import { createIntentMachine, type IntentMachine } from './intents/machine.ts';
import { draftSettlement } from './intents/draftSettlement.ts';
import { sendOnce } from './intents/send.ts';
import { createSequence } from './sequence.ts';
import {
  browserSessionStorage,
  prefixedStorage,
  readJsonItem,
  writeJsonItem,
  type KeyValueStorage,
} from './storage.ts';
import { fetchTransport, type Transport } from './transport.ts';
import { bodyMembers, ENDPOINTS, type Endpoint } from './wire.ts';
import {
  profileTargetConfigAnswerSchema,
  profileTargetSourceAnswerSchema,
  profileTargetMoreSourceAnswerSchema,
} from '../contract/profileTarget.ts';
import {
  scheduleConfigAnswerSchema,
  schedulePatchAnswerSchema,
  schedulePlanAnswerSchema,
  scheduleProfileAnswerSchema,
} from '../contract/schedule.ts';
import { scheduleStatusAnswerSchema } from '../contract/dashboard.ts';
import { planDeleteSourceAnswerSchema } from '../contract/r12Families.ts';

const sourceSchemas = [
  profileTargetMoreSourceAnswerSchema,
  profilePatchFencedSourceAnswerSchema,
  profileTargetSourceAnswerSchema,
  scheduleProfileAnswerSchema,
  schedulePatchAnswerSchema,
  schedulePlanAnswerSchema,
  // F6-11 RL8: the profile reads around a removal of the whole plan (r12 candidate).
  planDeleteSourceAnswerSchema,
];
const source: Endpoint = {
  id: 'config',
  method: 'GET',
  strict: sourceSchemas,
  members: bodyMembers(sourceSchemas),
};
const config: Endpoint = {
  id: 'config',
  method: 'GET',
  strict: [scheduleConfigAnswerSchema, profileTargetConfigAnswerSchema],
  members: bodyMembers([scheduleConfigAnswerSchema, profileTargetConfigAnswerSchema]),
};
const status: Endpoint = {
  id: 'schedule',
  method: 'GET',
  strict: [schedulePlanAnswerSchema, scheduleStatusAnswerSchema],
  members: bodyMembers([schedulePlanAnswerSchema, scheduleStatusAnswerSchema]),
};

/** The shared draft/intent scope excludes the active binding and includes the edited name. */
function scheduleKey(context: PageContext, name = context.profile): string {
  return name === null ? contextKey(context) : editTargetScope(context, name);
}

/**
 * F6-11 RL8: one explicit removal of the whole plan section ({op:"delete"} on ["plan"], r12
 * candidate plan_section_delete), through the same record kind as a plan save: a later read
 * without a plan settles it, as one holding a sent plan settles a save.
 */
export function sendScheduleRemove(
  machine: IntentMachine,
  context: PageContext,
  revision: string,
  name = context.profile,
) {
  const target = `schedule:${scheduleKey(context, name)}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'schedule_save',
      target,
      endpoint: 'profilePatch',
      path: '/api/profile-patch',
      body: {
        name,
        path: ['plan'],
        op: 'delete',
        expected_revision: revision,
        expected_context: expectedContext(context),
      },
      context,
      key: null,
      meta: { profileRevision: revision },
      supersedes: [],
    }),
  );
}

/** One explicit whole-plan save, using the shared duplicate-submission guard. */
export function sendScheduleSave(
  machine: IntentMachine,
  context: PageContext,
  revision: string,
  plan: unknown,
  name = context.profile,
) {
  const target = `schedule:${scheduleKey(context, name)}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'schedule_save',
      target,
      endpoint: 'profilePatch',
      path: '/api/profile-patch',
      body: {
        name,
        path: ['plan'],
        value: plan,
        expected_revision: revision,
        expected_context: expectedContext(context),
      },
      context,
      key: null,
      meta: { profileRevision: revision },
      supersedes: [],
    }),
  );
}

export function createScheduleApi(
  transport: Transport = fetchTransport,
  storage: KeyValueStorage | null = browserSessionStorage(),
  windowMs?: number,
) {
  const sequence = createSequence();
  // F6-14b: every patch carries the runtime fence; the click-time check stays as a
  // second guard. Inactive writes also check that the named target is still listed.
  const readClickContext = createClickTimeContextRead(transport, config);
  const checkTarget = createClickTimeTargetCheck(transport, {
    config,
    profileFiles: ENDPOINTS.profileFiles,
  });
  const machine = createIntentMachine({
    transport,
    storage: prefixedStorage(storage, 'tp.schedule:'),
    sequence,
    ...(windowMs === undefined ? {} : { windowMs }),
  });
  return {
    machine,
    draftSettlement,
    sequence,
    targetKey: scheduleKey,
    checkTarget,
    configMatches: (body: unknown, context: PageContext) =>
      sameContext(contextFromConfig(body), context),
    reportMatches: (body: unknown, context: PageContext) =>
      sameContext(readStoredContext(own(body, 'context')), context),
    readConfig: (signal: AbortSignal) => readEndpoint(transport, config, '/api/config', signal),
    /**
     * Whether a config read started now (never joined to an earlier read) still names exactly
     * `context`: `unconfirmed` when it has no usable answer.
     */
    checkContext: async (context: PageContext): Promise<'same' | 'changed' | 'unconfirmed'> => {
      const named = await readClickContext();
      return named === null ? 'unconfirmed' : sameContext(named, context) ? 'same' : 'changed';
    },
    readProfile: (name: string, signal: AbortSignal) =>
      readEndpoint(transport, source, `/api/profile-src/${encodeURIComponent(name)}`, signal),
    readStatus: (context: PageContext, signal: AbortSignal) =>
      readEndpoint(
        transport,
        status,
        `/api/schedule/status?instance=${encodeURIComponent(context.instance)}`,
        signal,
      ),
    send: (context: PageContext, revision: string, plan: unknown, name = context.profile) =>
      sendScheduleSave(machine, context, revision, plan, name),
    remove: (context: PageContext, revision: string, name = context.profile) =>
      sendScheduleRemove(machine, context, revision, name),
    loadDraft: (key: string) => readJsonItem(storage, `tp.schedule.draft:${key}`),
    keepDraft: (key: string, draft: unknown) =>
      writeJsonItem(storage, `tp.schedule.draft:${key}`, draft),
  };
}
export type ScheduleApi = ReturnType<typeof createScheduleApi>;
