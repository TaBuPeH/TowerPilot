import { text } from './read.ts';

/**
 * Opaque tokens (safety rule S-016): idempotency keys, `request_key`,
 * `policy_revision` and `handoff_id`. They are compared for equality only:
 * never parsed, displayed, ordered or turned into server text. A token is an
 * empty frozen object with a type-only brand; its characters live in a
 * module-private map, so nothing renders, serialises or orders it, and
 * `sameToken` is the only comparison.
 */
declare const tokenBrand: unique symbol;

/** An opaque token. */
export interface OpaqueToken {
  readonly [tokenBrand]: true;
}

const characters = new WeakMap<OpaqueToken, string>();

/** A token from a raw value: any non-empty string, whatever its format; else null. */
export function opaqueToken(raw: unknown): OpaqueToken | null {
  const value = text(raw);
  if (value === undefined || value === '') return null;
  const token = Object.freeze({}) as OpaqueToken;
  characters.set(token, value);
  return token;
}

/** Equality, the only comparison a token has. */
export function sameToken(a: OpaqueToken | null, b: OpaqueToken | null): boolean {
  if (a === null || b === null) return false;
  const left = characters.get(a);
  return left !== undefined && left === characters.get(b);
}

/**
 * The token's characters, for exactly one use: placing the token back into a
 * request body (api/, plan task R8). Never for display, parsing or ordering.
 */
export function tokenWireValue(token: OpaqueToken): string {
  return characters.get(token) ?? '';
}
