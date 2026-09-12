# GitHub skills research - 2026-08-18

Three-agent sweep (Python craft / ADB-mobile / collection landscape) for
importable Claude Code skills. Everything below is reference; imported
skills live in `.claude/skills/` with provenance headers.

## Imported (8, slim per user mandate: distill not mirror, ~120-line cap)

Python: python-performance-optimization 80L (jacexh/skills),
pytest-practices 93L (aks-builds/quality-skills), python-typing-ops 99L
(0xDarkMatter/claude-mods), python-code-quality 79L (wdm0006/python-skills),
opencv-image-processing 77L (aeren23/image-processing-skills, 2 skills
folded into 1), tighten-types 77L + hypothesis-tests 78L (honnibal/
claude-skills, explicit-invocation only).
ADB: adb-craft 97L - FOUR sources merged into one (skydoves
connecting-to-devices + injecting-input-and-state + scripting-adb-for-ci,
plus httprunner android-adb quick reference). 680 lines total for the
whole craft layer.

## Deferred - revisit when relevant

- **scrcpy-mcp** (juancf/scrcpy-mcp): MCP server, ~33ms screenshots vs our
  ~350ms adb screencap. An INFRASTRUCTURE upgrade for capture.py, not a
  skill. Evaluate when capture latency matters (rescue timing headroom).
- **adb-android-control** (hah23255): excellent, 338 tests, but built
  around its own Python package/CLI - conflicts with our socket-first
  adbclient.py. Mine its SKILL.md for patterns if needed.
- **claude-in-mobile** (AlexGladkov): cross-platform MCP (Android/iOS/
  desktop). Overkill for MuMu-only today.
- **honnibal remaining skills**: mutation-testing, pre-mortem,
  contract-docstrings, stub-package, try-except - adopt when a test suite
  exists to mutate.
- **wdm0006/python-skills**: 14-skill collection; cherry-picked one. Others
  (packaging, APIs, CLIs, security-audit) if the autopilot becomes a
  distributable package.
- **odinokov/research-to-package**: scripts -> installable library
  discipline; relevant if autopilot/ gets refactored into a package.
- **appium-skill** (LambdaTest): locator hierarchy principles (semantic >
  pixel) - conceptual influence only, we are vision-based by design.
- **steve1316/android-cv-bot-template + android-cv-automation-library**:
  on-device CV (MediaProjection + AccessibilityService) - blueprint if
  vision ever moves onto the device.

## Collections index (for future rounds)

- anthropics/skills - official, Apache 2.0
- hesreallyhim/awesome-claude-code - biggest curated index
- karanb192/awesome-claude-skills - verification badges
- alirezarezvani/claude-skills - 362 skills, stdlib-only philosophy
- rohitg00/awesome-claude-code-toolkit - production toolkit
- skydoves/android-testing-skills - the Android/ADB bible (54 skills)

Ecosystem verdict: Python mature (import > write), ADB fragmented but
usable, vision/emulator-specific (MuMu, template-matching craft) empty -
author our own from runbook experience.
