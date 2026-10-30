# Build provenance and release identity rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-24
> **Updated:** 2026-10-24
> **Owner:** runtime/build_info.py

## Contract

[build_info.py](build_info.py) owns what a process or an install can say about the source it runs.
Routes and the environment report transport its answers; they never read or hash these files themselves.

- **Source fingerprint** (`source_fingerprint`): SHA-256 over the `.py` files under `backend/` and `frontend/`
  (relative path and bytes; tests, caches, account and state folders, `node_modules` excluded). `snapshot` takes it
  once when a process starts; `compare` asks again later. It describes source on disk at that moment, not every
  imported module. `tools/` and `v2.py` are outside it.
- **Release identity** (`release_identity`): what `release_identity.json` at the release root says, or `checkout`
  when there is none. The shape never changes: every field of `IDENTITY_FIELDS` (null when absent), `matches_source`,
  `matches_app_build` and `error`.

## The identity file

Written by `tools/build_release.py` into both ZIPs (`write_identity`, over the staged tree that is zipped) and listed in
`SHA256SUMS.txt`: `schema_version`, `version`, `kind` (`source` or `portable-windows-x64`), `source_fingerprint`
(this module's function over the staged tree), `app_build_sha256` (SHA-256 of `BUILD_INVENTORY.txt`), `lock_sha256`,
`python` (portable only), `built_at`, `source_revision`, `working_tree`. It carries no path, user or machine name.
The writer refuses a field or kind this module does not list.

Both builders refuse unless `working_tree` is `clean` (`git status` at build time; an untracked file git does not
ignore counts as dirty; no git means `unknown`). `--allow-dirty` is for rehearsals and records `dirty` or `unknown`; the archive
gate then rejects the ZIP. A tree that has no `.git` of its own is `unknown` even inside another repository.

## The two comparisons

- `matches_source`: the recorded fingerprint against a fresh `source_fingerprint` of the root. An edited, added or
  removed `.py` file reads false. A record without a fingerprint reads false; files that cannot be read read null.
- `matches_app_build`: `BUILD_INVENTORY.txt` against the recorded hash, then every file under `frontend/webui/app/`
  against the inventory's lines, in both directions. An edited, missing or extra file reads false even when the inventory
  was left alone; unreadable files read null.

`verify=False` reads the record only (kind and fields, both comparisons null). The environment report uses it: it needs
the kind on every poll, not the hashing.

## Kinds

`checkout` (no file), `source`, `portable-windows-x64`, and `unknown` (a file that cannot be read, is not JSON, is not an
object or names another kind; `error` says which, without a path). An unreadable identity is never reported as a checkout.

## Limits

The file is not signed: someone who edits an install can edit the identity with it, and the archive's `SHA256SUMS.txt`
and sidecar are what protect the release in transit. The tools and the server cannot import each other (the archive gate runs from a lone ZIP with no
checkout, `test_single_artifact_runs_without_checkout_or_application_imports`), so
the inventory rules exist in `tools/build_release.py` (`_inventory_problems`, on bytes in memory) and here (`_matches_app_build`, on disk);
`test_release_identity.py` runs both over the same mutations. The archive gate (`tools/check_release_zips.py`) requires the
identity of every archive: present, version, kind, app-build hash and `clean`. It does not recompute the fingerprint; `/api/runtime.release` does,
in the installed copy.
