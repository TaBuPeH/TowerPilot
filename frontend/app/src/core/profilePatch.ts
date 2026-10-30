import { entries, id, own, stringList, text } from './read.ts';
import { sameJson } from './json.ts';

/**
 * `POST /api/profile-patch` (a path inside the active profile's source, a whole value or a
 * delete, and the profile revision it was made from): how its answer settles the request,
 * and what a later read of the profile source says about one whose answer was lost. Shared
 * by every page that patches the profile (Schedule, Collection, Abilities). The patch names
 * a profile, not the page context: the caller fences the target before it sends.
 */
export type ProfilePatchAnswer =
  /** 200 with the new profile and revision: the patch is in the file. */
  | {
      readonly kind: 'done';
      readonly revision: string | null;
      readonly backup: string | null;
      readonly warnings: readonly string[];
    }
  /** 4xx with `ok: false`: this request changed nothing (the validator's problems listed). */
  | {
      readonly kind: 'refused';
      readonly status: number;
      readonly code: string | null;
      readonly error: string | null;
      readonly problems: readonly string[];
    }
  /** No receipt: a 5xx, a text page, a 200 without the profile and revision. */
  | { readonly kind: 'unknown' };

export function profilePatchAnswer(answer: {
  readonly status: number;
  readonly body: unknown;
}): ProfilePatchAnswer {
  const body = answer.body;
  if (
    answer.status === 200 &&
    own(body, 'ok') === true &&
    typeof own(body, 'revision') === 'string' &&
    typeof own(body, 'profile') === 'object' &&
    own(body, 'profile') !== null
  )
    return {
      kind: 'done',
      revision: id(own(body, 'revision')) ?? null,
      backup: text(own(body, 'backup')) ?? null,
      warnings: stringList(own(body, 'warnings')) ?? [],
    };
  if (answer.status >= 400 && answer.status < 500 && own(body, 'ok') === false)
    return {
      kind: 'refused',
      status: answer.status,
      code: id(own(body, 'code')) ?? null,
      error: text(own(body, 'error')) ?? null,
      problems: stringList(own(body, 'problems')) ?? [],
    };
  return { kind: 'unknown' };
}

/** A patch as its request body froze it. */
export interface SentPatch {
  readonly path: readonly string[];
  /** `delete` removes the path; otherwise `value` is written there. */
  readonly remove: boolean;
  readonly value: unknown;
  /** The profile revision the patch was made from. */
  readonly revision: string | null;
  /** The profile it names: a read of another profile says nothing about it. */
  readonly name?: string | undefined;
}

/** The patch a request body describes, or null when it is not one. */
export function sentPatch(body: unknown): SentPatch | null {
  const path = stringList(own(body, 'path'));
  if (path === undefined || path.length === 0) return null;
  return {
    path,
    remove: own(body, 'op') === 'delete',
    value: own(body, 'value'),
    revision: id(own(body, 'expected_revision')) ?? null,
    name: id(own(body, 'name')),
  };
}

/** The value at `path` inside a profile, or undefined when the path is absent. */
export function atPath(profile: unknown, path: readonly string[]): unknown {
  let current: unknown = profile;
  for (const key of path) {
    if (Array.isArray(current) || !entries(current).some(([name]) => name === key))
      return undefined;
    current = own(current, key);
  }
  return current;
}

/**
 * What a later profile source read (`GET /api/profile-src/<name>`, started after the patch)
 * says about a patch whose answer was lost: `applied` when the read is of the profile the
 * patch named and the path holds exactly the sent value (or is gone, for a delete); null
 * otherwise. An unchanged revision never proves the patch failed: the original request may
 * still be running and commit later (review F6C-R1-02), so the patch stays unknown.
 */
export function settleProfilePatch(readBody: unknown, sent: SentPatch): 'applied' | null {
  const profile = own(readBody, 'profile');
  const revision = id(own(readBody, 'revision'));
  if (profile === undefined || revision === undefined) return null;
  if (sent.name === undefined || id(own(readBody, 'name')) !== sent.name) return null;
  // The file must have changed since the revision the patch was made from: a read at that
  // same revision shows what was there before, not the patch (F6-14 brief section 3).
  if (sent.revision !== null && revision === sent.revision) return null;
  const found = atPath(profile, sent.path);
  return (sent.remove ? found === undefined : sameJson(found, sent.value)) ? 'applied' : null;
}
