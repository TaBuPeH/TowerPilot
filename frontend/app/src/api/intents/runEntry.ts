/** RL3/PE6 share the profile-patch writer and journal: named target, runtime fence,
 * one-send contract, raw-draft capture and read-only settlement. App glue checks the target. */
export { sendProfilePatch as sendRunEntryPatch } from './collection.ts';
