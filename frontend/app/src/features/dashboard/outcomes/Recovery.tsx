import { copy } from '../../../copy/catalogue.ts';
import type { RecoveryVM } from '../../../store/selectors/outcomes.ts';
import { Button } from '../../../ui/Button.tsx';
import { useDashboard } from '../context.ts';
import { StopButton } from '../shared/StopButton.tsx';

/** The server-selected way out, shared by owner notices and unfenced input owners. */
export function Recovery({ recovery }: { readonly recovery: RecoveryVM }) {
  const { commands } = useDashboard();
  if (recovery.kind === 'pause')
    return (
      <Button
        tier="halt"
        label={copy.dashboard.shell.revocations.pause.short}
        onClick={() => {
          void commands.pause();
        }}
      />
    );
  if (recovery.kind !== 'stop') return null;
  return (
    <StopButton
      stop={{
        key: `recovery:${recovery.operation ?? ''}`,
        kind: 'cancel',
        shown: true,
        enabled: recovery.command !== null,
        reason: recovery.command === null ? 'waiting_for_fresh_status' : null,
        sending: false,
        operation: recovery.operation,
        pid: null,
        lastKnown: false,
        origin: 'owner_notice',
        command: recovery.command,
      }}
    />
  );
}
