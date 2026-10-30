# Settings field rules
> **Status:** Active
> **Type:** Domain rules
> **Updated:** 2026-10-24

[settingsFields.ts](settingsFields.ts) owns metadata parsing, raw draft validation and
settings-save readback. Pages use its shared selector and SettingsModuleForm.

- Enum options come only from server metadata. Retain unlisted saved or draft values,
  including the empty string, with a visible reason; never display another option in
  their place. Unchanged fields are omitted from partial saves. An empty enum value
  is valid only when it is listed (the explicit instance preset fallback).
- String lists use one raw text row per entry. Names and order are preserved; unsupported
  saved names remain editable. Empty lists serialize as `[]`. Only non-blank strings and
  the server's `maxItems` limit are checked; names are never trimmed or coerced.
- Stable row keys and removal tombstones keep row identity in the existing scalar draft
  store. Editing snapshots the entire list, so later server reads cannot alter its draft.
  The journal stores the original raw list as internal provenance; the form edits text
  rows and sends typed arrays. A saved `false` member is distinct from explicit removal.
- A confirmed save clears a list atomically. If any row changed after submission, the
  draft's marker, rows and tombstones all remain; scalar fields still settle independently.
- Drafts and outcome records are scoped by context and module. A refused save retains the
  raw draft and exact server error. A lost answer needs a later matching-context read
  holding every submitted value, or explicit reconciliation; it never retries itself.

[settingsStringList.test.ts](../../tests/unit/core/settingsStringList.test.ts) exercises list
validation and settlement. Equipment's component and browser tests exercise the shared
control through the recorded tournament module.
