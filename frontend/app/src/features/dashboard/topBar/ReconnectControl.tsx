import { useId, useLayoutEffect, useRef } from 'react';
import { copy } from '../../../copy/catalogue.ts';
import type { TopBarStateVM } from '../../../store/selectors/topBar.ts';
import { Button } from '../../../ui/Button.tsx';
import { pageHref } from '../../../ui/routes.ts';
import { useDashboard } from '../context.ts';

const words = copy.dashboard.topBar;

/** Keeps reconnect gating and focus continuity together when configuration changes. */
export function ReconnectControl({
  vm,
  readOnly,
}: {
  readonly vm: TopBarStateVM;
  readonly readOnly: boolean;
}) {
  const { commands } = useDashboard();
  const reconnectSlot = useRef<HTMLSpanElement>(null);
  const focusedReconnect = useRef<HTMLElement | null>(null);
  const reasonId = useId();
  useLayoutEffect(() => {
    const previous = focusedReconnect.current;
    if (previous === null || previous.isConnected) return;
    focusedReconnect.current = null;
    // A read can replace the focused link with a command, or the command with a link.
    reconnectSlot.current?.querySelector<HTMLElement>('a[href], button')?.focus();
  }, [vm.reconnectAction, readOnly]);
  const reason =
    vm.reconnect.reason === 'process_listed'
      ? words.reconnectReasons.process_listed
      : vm.reconnect.reason === 'sending'
        ? words.reconnectReasons.sending
        : words.reconnectReasons.process_list_unavailable;
  return (
    <span
      className="tp-topbar-reconnect"
      ref={reconnectSlot}
      onFocusCapture={(event) => {
        focusedReconnect.current = event.target;
      }}
      onBlurCapture={(event) => {
        if (
          event.target.isConnected &&
          (!(event.relatedTarget instanceof Node) ||
            !event.currentTarget.contains(event.relatedTarget))
        ) {
          focusedReconnect.current = null;
        }
      }}
    >
      {vm.reconnectAction === 'choose_emulator' ? (
        <a href={pageHref('emulators')}>{words.chooseEmulator.short}</a>
      ) : (
        <>
          <Button
            tier="secondary"
            label={words.reconnect.short}
            onClick={() => {
              if (vm.reconnect.command !== null) void commands.reconnect(vm.reconnect.command);
            }}
            busy={vm.reconnect.sending}
            {...(vm.reconnect.enabled ? {} : { disabled: true, reasonId })}
          />
          {vm.reconnect.enabled ? null : <span id={reasonId}>{reason.short}</span>}
        </>
      )}
    </span>
  );
}
