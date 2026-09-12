# Markdown Format — Reference

> **Type:** Reference

Sidecar for [`SKILL.md`](SKILL.md). Contains long-form examples and verification scripts kept out of the main skill file for token economy.

---

## Example — Complete File

```markdown
# API Integration Notes

> **Status:** Active
> **Type:** Knowledge
> **Created:** 2026-04-27
> **Updated:** 2026-04-27
> **Owner:** Georgi
> **Tags:** api, integration

## Authentication

All calls use HTTP Basic Auth with `API_EMAIL` + `API_TOKEN`. See [credentials-notes](path/to/credentials.md) for credential details.

> [!warning] Token Expiry
> Tokens rotate every 90 days — set a calendar reminder.

---

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/rest/api/2/issue` | POST | Create issue |
| `/rest/api/3/search` | GET | Search |

---

## Related

- [other-notes](path/to/other.md)
```

---

## YAML Frontmatter — Never Use for Content Files

The following pattern is **forbidden** for content `.md` files:

```markdown
---
title: Refactor the Detection Pipeline
status: draft
created: 2026-04-27
tags:
  - refactor
---
```

Use the blockquote header instead (see `SKILL.md → Blockquote Metadata Header`).

---

## Verification Scripts

Quick checks from repo root (adjust the docs directory to this project's layout):

```bash
# Find content files that still start with YAML frontmatter
for f in $(find docs -name '*.md'); do
  [ "$(head -1 "$f")" = "---" ] && echo "YAML at top: $f"
done

# Find lingering wiki-links
grep -rn '\[\[' docs/  # should be empty

# Find inline hashtags on headers
grep -rnE '^#{1,6}.*#[a-z]' docs/  # should be empty
```
