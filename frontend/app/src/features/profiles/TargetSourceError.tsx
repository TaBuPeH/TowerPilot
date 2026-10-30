import { editingTarget as t } from '../../copy/editingTarget.ts';
import type { ServerText } from '../../copy/server.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';

/** The same read failure explains the disabled writes in each profile editor. */
export function TargetSourceError({ error }: { readonly error: ServerText<'message'> | null }) {
  return error === null ? null : (
    <div role="alert">
      <div>{t.sourceFailed.short}</div>
      <ServerValue kind="message" value={error} />
    </div>
  );
}
