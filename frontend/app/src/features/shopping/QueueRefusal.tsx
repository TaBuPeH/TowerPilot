import type { ServerText } from '../../copy/server.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';

/** The queue's server refusal stays visible next to its Save action. */
export function QueueRefusal({ message }: { readonly message: ServerText<'message'> | null }) {
  return message === null ? null : (
    <div role="status" data-queue-refusal>
      <ServerValue kind="message" value={message} />
    </div>
  );
}
