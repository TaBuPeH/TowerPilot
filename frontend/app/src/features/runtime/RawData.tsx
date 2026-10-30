import type { CopyText } from '../../copy/text.ts';
import type { ServerText } from '../../copy/server.ts';
import { runtime as t } from '../../copy/runtime.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';

/** The bounded raw-data view remains readable and scrollable from the keyboard. */
export function RawData({
  label,
  value,
}: {
  readonly label: CopyText;
  readonly value: ServerText<'detail'> | null;
}) {
  return (
    <pre
      role="region"
      aria-label={label}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Keyboard users need to scroll the bounded JSON block.
      tabIndex={0}
    >
      {value === null ? t.notReported.short : <ServerValue kind="detail" value={value} />}
    </pre>
  );
}
