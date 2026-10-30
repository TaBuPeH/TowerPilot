import { z } from 'zod';

/** Backend r10's common mutation-context refusals, shared by Profiles and Equipment. */
export const contextFenceAnswerSchema = z.union([
  z.strictObject({
    status: z.literal(409),
    body: z.strictObject({
      ok: z.literal(false),
      code: z.literal('context_changed'),
      error: z.string(),
      current_context: z
        .strictObject({
          instance: z.string().nullable(),
          account: z.string().nullable(),
          profile: z.string().nullable(),
          serial: z.string().nullable(),
        })
        .nullable(),
    }),
  }),
  z.strictObject({
    status: z.literal(400),
    body: z.strictObject({
      ok: z.literal(false),
      code: z.literal('invalid_request'),
      error: z.string(),
    }),
  }),
]);
