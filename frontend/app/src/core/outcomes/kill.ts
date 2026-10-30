import { own } from '../read.ts';

export type KillAnswerClass = {
  readonly kind: 'no_longer_running' | 'identity_unverified' | 'refused' | 'unknown';
  readonly code: unknown;
  readonly error: unknown;
  readonly resend: false;
};

/** S-060: gone never attributes a kill; no answer permits an automatic or identical resend. */
export function classifyKillAnswer(answer: unknown): KillAnswerClass {
  const status = own(answer, 'status');
  const body = own(answer, 'body');
  const code = own(body, 'code');
  const kind =
    status === 200 && own(body, 'gone') === true
      ? 'no_longer_running'
      : status === 409 && code === 'process_identity_unverified'
        ? 'identity_unverified'
        : status === 400 && (code === 'not_a_runner' || code === 'invalid_request')
          ? 'refused'
          : 'unknown';
  return { kind, code, error: own(body, 'error'), resend: false };
}
