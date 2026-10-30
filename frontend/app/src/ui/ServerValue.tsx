import type { ServerText, ServerTextKind } from '../copy/server.ts';
import type { CopyText } from '../copy/text.ts';
import './ui.css';
import './ServerValue.css';

export interface ServerValueProps<K extends ServerTextKind = ServerTextKind> {
  /** The server's text, as a store selector made it. */
  readonly value: ServerText<K>;
  /** Its kind (name and id: one line; message and detail: kept as sent); inferred from the value only, so it must match. */
  readonly kind: NoInfer<K>;
  /** A catalogue label shown before the value, outside the server-text marker. */
  readonly label?: CopyText;
  /** An id for the server text, so a control can name its target with aria-labelledby. */
  readonly id?: string;
}

/**
 * A native `<option>` whose label is server text (a run's name in a picker).
 * An option may hold text only, so the option itself carries the
 * `data-server-text` marker; its value is the code the picker sends.
 */
export function ServerOption({
  value,
  label,
}: {
  readonly value: string;
  readonly label: ServerText<'name'>;
}) {
  return (
    <option value={value} data-server-text="name">
      {label}
    </option>
  );
}

/**
 * The only component that renders server text (dashboard-spec 6.3). The text
 * sits alone in a <bdi data-server-text>, so its direction cannot reorder the
 * catalogue text around it and the render check knows it is server data.
 */
export function ServerValue<K extends ServerTextKind>({
  value,
  kind,
  label,
  id,
}: ServerValueProps<K>) {
  return (
    <span className="tp-server" data-kind={kind}>
      {label === undefined ? null : <span className="tp-server__label">{label}</span>}
      <bdi id={id} data-server-text={kind}>
        {value}
      </bdi>
    </span>
  );
}
