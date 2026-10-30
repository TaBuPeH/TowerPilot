import { adoptBody, launchBody, type AdoptRequest } from '../../core/emulators.ts';
import type { PageContext } from '../context.ts';
import type { IntentMachine } from './machine.ts';
import { sendOnce, type SendResult } from './send.ts';

/**
 * The Emulators page's guarded writes (emulators brief M2 to M5): one record per click, the
 * body frozen at the click, never resent (none carries an idempotency key). An adopt's lost
 * answer is settled by a later config read (store/fences.ts); a launch, a prepare and an
 * install are followed by reads that show what is running or installed, never by a resend.
 */
export type EmulatorWrite =
  | ({ readonly kind: 'adopt'; readonly baseRevision: string | null } & AdoptRequest)
  | {
      readonly kind: 'launch';
      readonly emulator: string;
      readonly index: string;
      /** The instance's name as the scan showed it (for the outcome line), or null. */
      readonly name: string | null;
    }
  | { readonly kind: 'prepare'; readonly index: string; readonly name: string | null }
  | { readonly kind: 'tools_install' };

/** The target a write replaces older lines on (one line per instance or VM). */
export function emulatorTarget(write: EmulatorWrite): string {
  switch (write.kind) {
    case 'adopt':
      return `emulators:adopt:${write.expectInstance}`;
    case 'launch':
      return `emulators:launch:${write.emulator}:${write.index}`;
    case 'prepare':
      return `emulators:prepare:${write.index}`;
    case 'tools_install':
      return 'emulators:tools';
  }
}

export function sendEmulatorWrite(
  machine: IntentMachine,
  write: EmulatorWrite,
  context: PageContext | null,
): SendResult {
  const target = emulatorTarget(write);
  const send = (
    endpoint: 'adopt' | 'launch' | 'prepare' | 'toolsInstall',
    path:
      | '/api/wizard/adopt'
      | '/api/wizard/launch'
      | '/api/wizard/bluestacks/prepare'
      | '/api/wizard/tools/install',
    body: Readonly<Record<string, unknown>>,
    meta: Readonly<Record<string, string>>,
  ) =>
    sendOnce(machine, target, context, () =>
      machine.send({
        kind: write.kind,
        target,
        endpoint,
        path,
        body,
        key: null,
        meta,
        context,
        supersedes: [],
      }),
    );
  switch (write.kind) {
    case 'adopt':
      return send('adopt', '/api/wizard/adopt', adoptBody(write), {
        serial: write.serial,
        instance: write.expectInstance,
        ...(write.baseRevision === null ? {} : { baseRevision: write.baseRevision }),
      });
    case 'launch': {
      const body = launchBody(write.emulator, write.index);
      if (body === null) return { kind: 'not_sent', reason: 'not_launchable' };
      return send('launch', '/api/wizard/launch', body, {
        emulator: write.emulator,
        index: write.index,
        ...(write.name === null ? {} : { name: write.name }),
      });
    }
    case 'prepare':
      return send(
        'prepare',
        '/api/wizard/bluestacks/prepare',
        { index: write.index },
        {
          emulator: 'BlueStacks',
          index: write.index,
          ...(write.name === null ? {} : { name: write.name }),
        },
      );
    case 'tools_install':
      return send('toolsInstall', '/api/wizard/tools/install', { accept_license: true }, {});
  }
}
