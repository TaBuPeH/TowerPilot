import { fenceProfileAnswerSchema } from '../contract/r12Families.ts';
import { own } from '../core/read.ts';
import { savedApplyRecordedIn } from '../core/recognition.ts';
import { readEndpoint } from './client.ts';
import { sameContext, type ContextTracker } from './context.ts';
import type { IntentMachine } from './intents/machine.ts';
import { GET_TIMEOUT_MS } from './poller.ts';
import type { Timers } from './timers.ts';
import type { Transport } from './transport.ts';
import { bodyMembers, type Endpoint } from './wire.ts';

const strict = [fenceProfileAnswerSchema];
const profileEndpoint: Endpoint = {
  id: 'profileSource',
  method: 'GET',
  strict,
  members: bodyMembers(strict),
};

/** Reads only: saved applies have no operation receipt. Never sends or retries a write. */
export function createRecognitionApplyReadback(
  machine: IntentMachine,
  transport: Transport,
  tracker: ContextTracker,
  timers: Timers,
) {
  const controllers = new Set<AbortController>();
  const checked = new Map<string, number>();
  const reading = new Set<string>();
  const readProfile = async (name: string): Promise<unknown> => {
    const controller = new AbortController();
    controllers.add(controller);
    const timeout = timers.setTimeout(() => {
      controller.abort();
    }, GET_TIMEOUT_MS);
    try {
      const result = await readEndpoint(
        transport,
        profileEndpoint,
        `/api/profile/${encodeURIComponent(name)}.yaml`,
        controller.signal,
      );
      // Profiles can have additional runs or policy keys beyond the recorded examples.
      // The core checks the frozen discoveries; unrelated shape drift cannot erase them.
      return result.kind === 'answered' && result.answer.status === 200 ? result.answer.body : null;
    } finally {
      timers.clearTimeout(timeout);
      controllers.delete(controller);
    }
  };
  return {
    readProfile,
    /** Called on a later calibration read (also after reload); clock ticks do not poll. */
    check: (readSeq: number) => {
      for (const record of machine.snapshot()) {
        const context = record.context;
        if (
          record.kind !== 'calibrate_apply' ||
          record.meta.action !== 'saved' ||
          record.phase !== 'unconfirmed' ||
          record.readSettlement !== null ||
          record.supersededBySeq !== null ||
          context?.profile == null ||
          !tracker.confirmed() ||
          !sameContext(context, tracker.current()) ||
          record.meta.values === undefined ||
          reading.has(record.id) ||
          checked.get(record.id) === readSeq
        )
          continue;
        checked.set(record.id, readSeq);
        reading.add(record.id);
        const epoch = tracker.epoch();
        void readProfile(context.profile)
          .then((after) => {
            if (
              tracker.epoch() === epoch &&
              sameContext(context, tracker.current()) &&
              savedApplyRecordedIn(
                own(record.meta.values, 'before'),
                own(record.meta.values, 'observations'),
                after,
              )
            )
              machine.settleByRead(record.id, 'applied');
          })
          .finally(() => {
            reading.delete(record.id);
          });
      }
    },
    stop: () => {
      for (const controller of controllers) controller.abort();
    },
  };
}
