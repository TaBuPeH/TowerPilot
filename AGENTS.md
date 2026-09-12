# Agent instructions (Codex, Claude Code and any other coding agent)

The project rules live in [CLAUDE.md](CLAUDE.md). Read it first; its eight
hard rules have each cost real game runs and apply to every agent, whatever
tool it runs in.

Skills and workflow guidance live in `.claude/`:

- `.claude/skills/<name>/SKILL.md` - one skill per folder. Load every skill
  whose trigger in `.claude/skills/trigger-map.md` matches the task before
  writing code. `verify-never-infer` is mandatory before reporting any
  factual claim about code, data or process state.
- `.claude/agents/` - role definitions for sub-agents.
- `.claude/hooks/` and `.claude/settings.json` - Claude Code hooks (skill
  reminders, post-compaction reload gate, token tracking). Codex has no hook
  runner; apply the same discipline by hand.
- `.codex/config.toml` - project-local Codex configuration.

Verification habits: `python -m py_compile <files>` after edits, `python -m
pytest` from the repo root (offline), `node --test frontend/tests/*.test.cjs`
for the dashboard. Machine and account state (config, profiles, logs,
templates, captures) never enter git - see `.gitignore`.
