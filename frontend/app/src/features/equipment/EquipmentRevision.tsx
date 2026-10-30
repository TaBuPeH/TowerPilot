import type { ServerText } from '../../copy/server.ts';
import { equipment as t } from '../../copy/equipment.ts';
import { ServerValue } from '../../ui/ServerValue.tsx';

/**
 * A configuration revision is a long hex id: it is not abbreviated or clipped, so
 * its exact value is one disclosure away and wraps over as many lines as it needs.
 */
export function EquipmentRevision({ value }: { readonly value: ServerText<'id'> | null }) {
  if (value === null) return t.unknown.short;
  return (
    <details className="tp-equipment__revision tp-disclosure">
      <summary>{t.fullRevision.short}</summary>
      <code className="tp-equipment__revision-full">
        <ServerValue kind="id" value={value} />
      </code>
    </details>
  );
}
