import { Server, Socket } from 'node:net';
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { toHaveNoViolations } from 'jest-axe';
import { afterEach, expect } from 'vitest';
// Starts the render check listening to the copy catalogue before any test renders.
import './copy/renderedText.ts';

expect.extend(toHaveNoViolations);

afterEach(() => {
  cleanup();
});

// No test opens a socket (R2h). On Node 24 for Windows a Vitest worker that opens loopback TCP
// connections sometimes dies natively right after a Socket#connect call (exit code 0xC0000409,
// no message), which made `npm test` fail intermittently. Drive HTTP handlers in memory, as
// tests/unit/tooling/fake-backend.test.ts does; real sockets belong to the Playwright suite.
// net, http(s), tls, fetch and WebSocket all connect through Socket#connect, and every server
// listens through Server#listen.
function refuseSockets(): never {
  throw new Error(
    'Tests must not open sockets (R2h): on Node 24 for Windows they can crash the test worker. ' +
      'Drive the handler in memory, or test it with Playwright.',
  );
}
Socket.prototype.connect = refuseSockets;
Server.prototype.listen = refuseSockets;
