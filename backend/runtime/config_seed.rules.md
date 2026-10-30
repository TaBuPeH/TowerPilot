# Config seed rules
> **Status:** Active
> **Type:** Rules
> **Created:** 2026-10-25
> **Updated:** 2026-10-25
> **Owner:** runtime/config_seed.py

## Contract

[config_seed.py](config_seed.py) owns how a missing `backend/config.yaml` is created from the shipped
`backend/config.example.yaml`. `settings.seed_config` and the dashboard's `seed_config` are thin callers: they pass
their own paths and keep their own call sites (settings seeds at import; the dashboard seeds before it reads).
The dashboard never imports `settings`, so the rule lives where both can import it with no side effect.

- **Byte copy.** The seeded file has exactly the bytes of the example. A text-mode copy turns LF into CRLF on
  Windows, and a portable install is checked against the shipped example byte for byte.
- **Never replaces.** An existing `config.yaml` is left alone and the call answers False, including a file created by
  another starter between the check and the write.
- **Fails loudly.** With neither file present the call raises `FileNotFoundError` naming both paths. A failed write
  removes the partial file it made, so a retry seeds again instead of reading half a config.
- **No account data.** The example ships generic placeholders only (`CLAUDE.md` rule 8; `test_release_clean.py`).
