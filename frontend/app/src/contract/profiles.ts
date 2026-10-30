import { z } from 'zod';
import { contextFenceAnswerSchema } from './contextFence.ts';
import {
  profilesAccountsPostAnswerSchema as accountsPost,
  profilesActivatePostAnswerSchema as activatePost,
  profilesCopyPostAnswerSchema as copyPost,
  profilesPromotePostAnswerSchema as promotePost,
  profilesTierPostAnswerSchema as tierPost,
} from './generated/dashboard_contract_pages_v1_profiles.a26ad877.ts';

/**
 * The Profiles page's contract (profiles brief P1 to P5): strict schemas
 * generated from the accounts_write, profile_files_write, profile_reads and
 * config_state families of backend/tests/fixtures/dashboard_contract_pages_v1.json,
 * bound to those families' hashes. Each is the answer `{status, body}` of one
 * route at raw response path `answer`.
 */
export {
  profilesAccountsReadAnswerSchema,
  profilesConfigReadAnswerSchema,
  profilesFilesAfterAccountAnswerSchema,
  profilesFilesAfterWriteAnswerSchema,
  profilesFilesReadAnswerSchema,
  profilesSourceAfterPromoteAnswerSchema,
  profilesSourceAfterTierAnswerSchema,
  profilesSourceReadAnswerSchema,
} from './generated/dashboard_contract_pages_v1_profiles.a26ad877.ts';

export const profilesAccountsPostAnswerSchema = z.union([accountsPost, contextFenceAnswerSchema]);
export const profilesActivatePostAnswerSchema = z.union([activatePost, contextFenceAnswerSchema]);
export const profilesCopyPostAnswerSchema = z.union([copyPost, contextFenceAnswerSchema]);
export const profilesPromotePostAnswerSchema = z.union([promotePost, contextFenceAnswerSchema]);
export const profilesTierPostAnswerSchema = z.union([tierPost, contextFenceAnswerSchema]);
