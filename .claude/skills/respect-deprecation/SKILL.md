---
name: respect-deprecation
description: >-
  `@deprecated` / `# DEPRECATED:` / `DeprecationWarning` symbol guard.
  Use when importing, instantiating, extending, or calling ANY type, class,
  function, enum, constant, config key, or data column — STOP and ask the user
  before touching a deprecated symbol.
---

# Respect Deprecation — Read the Deprecation Tags Before You Use Anything

**MANDATORY: Before you import, instantiate, extend, or call any symbol — type, class, function, enum, constant, config key, or data column — check whether it carries a deprecation marker (`@deprecated` docstring/JSDoc tag, a `# DEPRECATED:` / `// DEPRECATED:` comment, a `DeprecationWarning`, or a deprecated flag in config/schema). If it does, STOP. Do not use it. Ask the user which replacement to use and whether the deprecated version should still be touched.**

## When This Skill Applies

Triggers — load and consult this skill before:

- Importing any symbol from a module you have not audited for deprecation markers
- Calling a method on an existing class or service
- Extending a base class or reusing a shared helper
- Reading a field from a data model or schema
- Using a value from an enum
- Working in any file that already imports symbols — verify the existing imports are not deprecated before adding new code that uses them

**The rule is symmetric**: if the file you are editing already uses a deprecated symbol, you must NOT pile on more usages of it. Surface the deprecation to the user instead.

## The Core Rule

> **Deprecation always means a replacement exists.** Nothing is marked deprecated for fun — when you see the tag, there is always a newer interface, module, or method that should be used instead. Your job is to find it, confirm it with the user, and use the replacement.

## How to Detect Deprecation

| Where to look | What to look for |
|---|---|
| Docstrings / JSDoc above a declaration | `@deprecated` tag, usually naming the replacement |
| Inline comments | `# DEPRECATED:` / `// DEPRECATED:` comment blocks |
| Runtime warnings | `DeprecationWarning` / `warnings.warn(..., DeprecationWarning)` emissions |
| Header comments at the top of a file | `# DEPRECATED FILE — replaced by ...` |
| Schema / config definitions | deprecated flags or comments on fields |
| Enum members | deprecation note on the member |

## The Procedure

When you encounter a deprecated symbol that's relevant to the work you're about to do:

```
1. STOP — do not write the import or usage yet
2. Read the deprecation comment in full — it usually names the replacement
3. If the replacement is named, locate it:
   - Grep the codebase for the replacement's definition and exports
   - Verify the replacement exists and is not itself deprecated
4. Surface to the user with this exact format:

   ⚠️ Deprecated symbol detected: <SymbolName>
   Location: <file:line of the deprecation tag>
   Replacement (per the comment): <ReplacementName> at <file:line>
   Status of replacement: <verified exists / unclear>

   Question: Should I use <ReplacementName>, or are there reasons to keep using
   the deprecated <SymbolName> for this specific change (e.g., the surrounding
   code still uses the old type and migrating it is out of scope)?

5. WAIT for the user's answer — do not assume
6. If the user says use the replacement → use it, and consider whether the surrounding
   deprecated usage should be migrated (often yes, sometimes deferred as a pending task)
7. If the user says keep the deprecated one for now → use it, and create a pending task
   to migrate it later (per the pending-tasks skill)
```

## Special Cases

| Situation | What to do |
|---|---|
| The deprecation comment is vague ("use the new one") with no name | Surface to user with the question "what is the intended replacement for X?" |
| The replacement would cascade through many imports | Surface to user; this is a migration task, not a one-line edit |
| The whole file is deprecated (header comment) | Do not edit it unless the task is explicitly to delete or migrate it. Surface to user. |
| A test file uses a deprecated symbol | Same rule applies — tests should also use the replacement when possible |
| You need to *delete* a deprecated symbol | Allowed and welcome — but verify no live code references it, including indirect references (registrations, string-keyed lookups, dynamic imports). Confirm with the user. |
| Generated code marks something deprecated | The deprecation came from the source definition. Treat it as authoritative — same rule applies. |

## Anti-Patterns

| Anti-Pattern | Why It's Wrong |
|---|---|
| "The file already imports the old symbol, so I'll just add another usage" | Adds to migration debt, hides the deprecation signal |
| "I'll use the deprecated type because the new one looks more complex" | Complexity is the migration cost — surface it, don't dodge it |
| "The deprecation comment doesn't name a replacement, so I'll assume there isn't one" | Ask the user; assume the comment is incomplete, not that the deprecation is fictional |
| Removing the deprecation tag because "it works fine" | The tag is a contract — only the original author or an explicit decision can remove it |
| Migrating ALL usages in a file when the user only asked for a small change | Scope creep. Surface the larger migration as a pending task and stay focused. |
| Silently using the deprecated symbol because "it's still in the codebase" | Existing usage ≠ approved usage. Existing usage is debt. |
| Picking the "newest looking" replacement without confirming with the user | Multiple replacements may exist; the right one depends on context |

## Verification

After completing a code change, verify:

1. **No new deprecated imports** — grep your diff for any symbol that has a deprecation marker upstream
2. **Existing deprecated imports left in place** — only acceptable if the user explicitly approved keeping them or you created a pending task for the migration
3. **If you used a replacement** — confirm the import path is the canonical one (not a re-export from a deprecated module)
4. **If the surrounding file has lingering deprecated usages** — note them in your summary so the user knows the file still has migration debt

## See Also

- [correct-fix-over-easy-fix](../correct-fix-over-easy-fix/SKILL.md) — using a deprecated symbol because "it's easier" is the textbook easy fix; the correct fix is the replacement
- [pending-tasks](../pending-tasks/SKILL.md) — when a migration is out of scope, create a pending task instead of silently adding more deprecated usages
