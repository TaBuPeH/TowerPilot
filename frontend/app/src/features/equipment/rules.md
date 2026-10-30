# Equipment editor rules
> **Status:** Active
> **Type:** Domain rules
> **Updated:** 2026-10-23

Equipment edits one existing saved loadout. It never applies equipment, starts a scan,
changes a runner, or claims that game equipment was inspected.

The store owns drafts and save reconciliation. `store/equipmentDraft.ts` owns selection
serialization and discovery membership; the backend owns ownership validation and entry
behavior. The component renders the selector's results and sends explicit user events.

- Drafts, original bodies, raw selections and immutable submissions belong to an emulator,
  serial, account, profile and loadout name. Navigation and reload keep them under that target.
- Blank category selections omit the field. No default, trimming or ownership inference is
  inserted. Missing saved discoveries remain selected and prevent Save.
- Manual selections remain read-only; restore cards and ordered module/slot rows extend
  the original body without changing untouched keys. A conversion requires a before/after review of removed
  fields; changing the draft or reconciling its original body invalidates that review.
  Restore rows retain their original raw values and extra members across sibling edits
  and reload. Only explicitly edited cells are replaced; malformed rows show their raw
  content with associated validation errors and can be removed and replaced explicitly.
- Restore eligibility uses only the active profile's source blueprint loadout references.
  Changes, including clearing, are blocked for an unused loadout or no active profile.
  A readable empty blueprint mapping proves that every loadout is unused. Unreadable source
  and absent, null or list-shaped blueprints defer to the server. Ordinary saves with unchanged restore values
  remain possible. Revision conflicts take precedence; server errors and warnings stay verbatim.
- Each Save sends one whole named body with its configuration revision through the shared
  intent machine. The isolated Equipment adapter uses its existing non-resendable `run_save`
  journal kind. No timer, read, navigation, reload or response sends a write.
- Profiles and Equipment share the click-time configuration reader and four-field context
  serializer. Each Save starts a fresh read, freezes the original submission, and sends only
  if the read and page still name its original context. The POST always carries exactly
  `instance`, `account`, `profile` and `serial` in `expected_context`.
  Changing the selected loadout or the original body/revision before sending cancels the
  reservation. Newer typing during that read stays a draft and does not cancel or replace
  the click's validated submission. A late success describes the submitted selection.
- A server `context_changed` refusal is Not saved, shows the reported current context, and
  retains the original target's draft. It requires a read and explicit reconciliation before
  another Save; it never retries a write.
- A successful response needs HTTP 200, `ok:true`, a nonempty configuration revision,
  and the exact submitted body under the submitted name. Fixture drift from arbitrary user
  names alone does not invalidate those checked fields. A no-op save can keep its revision.
  Newer typing stays unsaved.
- Lost answers are settled only by a later read of the original target and name. The matching
  submitted body at a changed revision means the saved selection is present. An unchanged
  body or revision keeps Unknown until explicit review; it cannot prove a delayed write failed.
- Config is read before and after the profile. Target, epoch, revision and sequence checks
  reject mixed or older reads. Reads older than 30 seconds cannot authorize a save.

Backend r10 checks the supplied context first under the configuration write lock. A mismatch
writes nothing. Configuration persistence still does not establish game equipment application;
live equipment verification is a separate gate.

Standing tournament tweaks are outside any named loadout and use the shared
[settings-field owner](../../core/settingsFields.rules.md). Their scope is the bound account
or legacy installation, never every account. Equipment's open-state wiring permits its
settings read without opening another page.
