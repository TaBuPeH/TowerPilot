import type { CopyNode } from '../../../copy/text.ts';
import { copy } from '../../../copy/catalogue.ts';
import { Region } from '../../../ui/Region.tsx';
import { FaultPoint, useEpochKey, usePartError } from '../context.ts';
import { RevocationFallback, type FallbackPart } from './RevocationFallback.tsx';

const regions = copy.dashboard.shell.regions;

export type DashboardRegionId = keyof typeof regions;

/**
 * One Dashboard region (spec 2.1) with its boundary wired: the region's
 * fallback keeps Pause harvesting and Turn input off (S-091; the Control
 * card's also keeps the known Stops), one diagnostic per distinct fault, a
 * new context epoch draws it again, and a test may make it fail (X4 to X6).
 */
export function DashboardRegion({
  id,
  children,
}: {
  readonly id: DashboardRegionId;
  readonly children: CopyNode;
}) {
  const part: FallbackPart = id === 'control' ? 'automation' : id;
  return (
    <Region
      id={id}
      title={regions[id].short}
      fallback={<RevocationFallback part={part} withStops={id === 'control'} />}
      resetKey={useEpochKey()}
      onError={usePartError(id)}
    >
      <FaultPoint part={id} />
      {children}
    </Region>
  );
}
