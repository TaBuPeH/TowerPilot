import { tapsSetting } from '../../core/config.ts';
import {
  allowInputOffered,
  gameInputSeen,
  inputOffOffered,
  type InputOffReason,
} from '../../core/input.ts';
import type { DashboardState } from '../types.ts';
import { chipVM, commandVM, type ChipVM, type CommandVM } from '../viewModels.ts';
import { controlFacts, latestOnTarget } from './controlFacts.ts';

/**
 * Control / Game input (spec 7.3, 3.6.3; INV-2, S-005, S-006, S-028, S-040,
 * S-047, S-048). Seen now never says Running; Idle only with EV-7 or EV-6.
 * Turn input off follows INV-2 whatever the chip says: rendered and enabled
 * whenever input may be on or its state is unknown, and every click is a new
 * request (it is never disabled while another write, an enable included, is
 * in flight: S-004, S-005).
 */
export type SettingVM =
  | { readonly known: true; readonly allowed: boolean }
  | { readonly known: false; readonly reason: string };

export interface InputSlotVM {
  readonly setting: SettingVM;
  readonly seen: ChipVM;
  readonly turnOff: CommandVM<null>;
  readonly offReason: InputOffReason | null;
  readonly allow: CommandVM<null>;
}

export function selectInputSlot(state: DashboardState): InputSlotVM {
  const facts = controlFacts(state);
  const seen = gameInputSeen(facts.input);
  const offReason = inputOffOffered(facts.input, seen);
  const taps = tapsSetting(facts.input.config);
  const latest = latestOnTarget(facts, 'input');
  const offSending = latest?.kind === 'input_off' && latest.phase === 'sending';
  const onSending = latest?.kind === 'input_on' && latest.phase === 'sending';
  return {
    setting:
      taps.kind === 'known'
        ? { known: true, allowed: taps.allowTaps }
        : { known: false, reason: taps.reason },
    seen: chipVM(seen),
    turnOff: commandVM(offReason !== null, offReason !== null, offReason, offSending, null),
    offReason,
    // F-017: Allow game input only while the Setting reads Off and a D-4 read shows no owner;
    // one request per click.
    allow: commandVM(
      allowInputOffered(facts.input),
      !onSending,
      onSending ? 'request_in_flight' : null,
      onSending,
      null,
    ),
  };
}
