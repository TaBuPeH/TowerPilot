import { copy } from '../../../copy/catalogue.ts';
import type { CopyNode } from '../../../copy/text.ts';
import { RegionBoundary } from '../../../ui/RegionBoundary.tsx';
import { RegionBody } from '../../../ui/Region.tsx';
import { Slot } from '../../../ui/Slot.tsx';
import { FaultPoint, useEpochKey, usePartError } from '../context.ts';
import { DashboardRegion } from '../shared/DashboardRegion.tsx';
import { RevocationFallback } from '../shared/RevocationFallback.tsx';
import { Outcomes } from '../outcomes/Outcomes.tsx';
import { AutomationSlot } from './AutomationSlot.tsx';
import { ControlAnnouncer } from './ControlAnnouncer.tsx';
import { ControlHeadEnd } from './ControlHeadEnd.tsx';
import { GemSlot } from './GemSlot.tsx';
import { InputSlot } from './InputSlot.tsx';

const slots = copy.dashboard.shell.slots;
type SlotId = keyof typeof slots;
const FALLBACK = { automation: 'automation', input: 'input', gem: 'gem' } as const;

/** One fixed slot with its own boundary: its fallback keeps its revocation (S-091, X4, X5). */
function ControlSlot({ id, children }: { readonly id: SlotId; readonly children: CopyNode }) {
  return (
    <Slot
      id={id}
      label={slots[id].short}
      fallback={<RevocationFallback part={FALLBACK[id]} withStops={id === 'automation'} />}
      resetKey={useEpochKey()}
      onError={usePartError(`slot:${id}`)}
    >
      <FaultPoint part={`slot:${id}`} />
      {children}
    </Slot>
  );
}

/**
 * Control (spec 2.1 region 1, C-1, C-9): the three fixed slots, each with its
 * state and its own revocation, then the outcome strip for the person's own
 * unresolved requests and the owner-refusal notice.
 */
export function ControlRegion() {
  return (
    <DashboardRegion id="control">
      <ControlHeadEnd />
      <RegionBoundary fallback={null} onError={usePartError('announcer')}>
        <ControlAnnouncer />
      </RegionBoundary>
      <RegionBody>
        <div className="tp-control__slots">
          <ControlSlot id="automation">
            <AutomationSlot />
          </ControlSlot>
          <ControlSlot id="input">
            <InputSlot />
          </ControlSlot>
          <ControlSlot id="gem">
            <GemSlot />
          </ControlSlot>
        </div>
        <RegionBoundary
          fallback={<RevocationFallback part="outcomes" />}
          resetKey={useEpochKey()}
          onError={usePartError('outcomes')}
        >
          <FaultPoint part="outcomes" />
          <Outcomes />
        </RegionBoundary>
      </RegionBody>
    </DashboardRegion>
  );
}
