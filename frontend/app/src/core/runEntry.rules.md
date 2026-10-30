# Saved run entry settings
> **Status:** Active
> **Created:** 2026-10-22
> **Updated:** 2026-10-23

`runEntry.ts` owns selection and parsing of a run's entry and equipment fields.
The server vocabulary owns legal fields per kind, dissonance tabs and numeric bounds.
Config owns available equipment; the profile owns policy references. Only coin runs
offer the vocabulary's special loadout. Count belongs only to shard runs.

Each field saves one profile patch. Raw drafts retain their starting revision and
survive reads and reload under their original context, run and field. Invalid text
is never clamped. Empty max wave writes null; an empty perk list clears bans;
deleting perk bans leaves the game's bans alone. None deletes a policy reference.

The shared profile-patch command fences context at click time, captures the raw
draft, sends once and journals the outcome. A 500 is Unknown. Only a later read
holding the sent change proves application. An unchanged read keeps Unknown;
explicit continuation permits a new save without changing that journal evidence.
Stale drafts require an explicit continuation from a later successful profile read.
Each revision pin also records the raw edit so that newer typing retains its original
revision when an older save clears only matching submitted draft keys. The marker
also restores the raw list and removal choice if a newer compound edit survives
while its unchanged submitted text key is cleared. The explicit
read action retries vocabulary as well as profile source after a failed initial read.
The routine editor remains a separate owner and save path.

RL9–RL13: Clear label is an explicit delete; empty labels never imply deletion.
Coin entries always offer dissonance tabs; the coin option deletes dissonant_tab,
including a stored null. Kind is never editable. Rides appear only for grant quests
and may be deleted to restore the quest default; cycles appear only for cycle quests
and cannot be deleted. These two values preserve numeric or quoted text input;
the server owns integer and range validation, and refusals keep the raw draft.
Inactive targets permit the entry saves recorded by `profile_target_more`: label,
equipment, tier, shard count, policy references, wave cap, restart and sprint flags,
dissonance tab, perk bans and tournament gem cap. Only perk bans, UW references and
shopping references have recorded inactive deletes. Quest values, label deletion,
gathering/rescue reference deletion and disabled-tab deletion retain a visible
active-profile reason until their fenced recordings exist. Policy links carry both
the family and policy identity to Abilities. The Chain Lightning ownership notice
matches the recorded validator's non-null, non-off mode predicate; it does not replace validation.
When rides is absent, the disabled quest-default action references visible catalogue copy
explaining that the quest already uses its default. Its gate includes the deletion check,
independently of the ordinary field save. Statistics is catalogue text with no control.

When the current source read fails, the entry selector may show the last answered
source through the existing editing-target selector, including its inactive
selection generation check. Reads belong to the current context epoch. This
history supplies field shape and retained drafts/outcomes only; it never grants
save, clear or continuation authority. The whole retained field is disabled and
the read failure uses existing client copy; server error text remains on the
editing-target line. A current successful source without a revision keeps its
fields editable but cannot offer Save. A healthy read for the same target restores
the existing explicit-action rules without sending.
Captured commands recheck the same entry selector's current source authority
immediately before sending, after click-time and target checks, so a failed read
cannot be bypassed by an older handler. Their reserved draft is unchanged; newer
typing does not replace it.
