import { contextKey, expectedContext, type PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce } from './send.ts';
import type { DraftValues } from '../../core/runControls.ts';

export interface RunAddInput {
  readonly runId: string;
  readonly revision: string;
  readonly template: string;
  readonly kind: string;
  readonly options: Readonly<Record<string, unknown>>;
  readonly scope: string;
  readonly draft: DraftValues;
}

/** RL4 fences the profile revision and click-time context. One click, one send; never resend. */
export function sendRunAdd(machine: IntentMachine, input: RunAddInput, context: PageContext) {
  const target = `run-add:${contextKey(context)}`;
  return sendOnce(machine, target, context, () =>
    machine.send({
      kind: 'run_add',
      target,
      endpoint: 'runTemplatesAdd',
      path: '/api/run-templates/add',
      body: {
        expected_revision: input.revision,
        expected_context: expectedContext(context),
        profile: context.profile,
        template: input.template,
        run_id: input.runId,
        options: input.options,
      },
      key: null,
      context,
      supersedes: [],
      meta: {
        baseRevision: input.revision,
        name: context.profile ?? '',
        blueprint: input.runId,
        preset: input.kind,
        draftScope: input.scope,
        draft: input.draft,
      },
    }),
  );
}
