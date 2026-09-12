# Writing Claude Skills — Best Practices from the LLM's Perspective

This document is written **from Claude's perspective as the consumer** of skill instructions. It explains how I process skills internally, what makes them effective or wasteful, and the principles that should guide skill authoring — independent of any existing skill library.

Use this as a benchmark to evaluate and improve existing skills.

---

## How I Actually Process a Skill

Understanding how I consume skill content is prerequisite to writing good skills. Here is what happens internally:

### Context window is finite real estate

Every token in a skill occupies space in my context window — the same window that holds the user's request, the code I'm reading, tool results, and my reasoning. A 1500-line skill that could be 400 lines doesn't just waste space — it actively degrades my performance on the actual task by leaving less room for code analysis and reasoning.

**Implication:** Conciseness is not a style preference. It directly affects output quality.

### I read top-to-bottom with decaying attention

Information at the **top** of a skill has the strongest influence on my behavior. Information buried deep in a long document may be partially attended to, especially under context pressure. This is not a bug — it's how attention mechanisms work in transformer architectures.

**Implication:** The most critical instruction must come first. If the single most important rule is on line 600, I may not weight it heavily enough when it matters.

### I follow instructions better than I infer intent

If you tell me "always use X instead of Y", I will do it reliably. If you describe X and Y and their tradeoffs and hope I choose X, I might choose Y in some contexts. Explicit directives produce more consistent behavior than implicit reasoning.

**Implication:** Be prescriptive. Say "do this" and "never do that." Don't describe the landscape and expect me to navigate it correctly every time.

### I pattern-match against examples

When I see a complete, working code example, I can reproduce the pattern with high fidelity — adapting names, paths, and specifics to the current context. When I see only a fragment or a verbal description of what the code should look like, I fill in the gaps from my training data, which may not match your project's conventions.

**Implication:** Complete code examples are the single highest-leverage thing you can put in a skill. One good example is worth paragraphs of explanation.

### I learn more from contrast than from instruction alone

Telling me "do X" is good. Showing me "here is X (correct) and here is Y (wrong, because Z)" is significantly better. The wrong example with its explanation creates a negative anchor — I actively avoid the pattern when I recognize it.

**Implication:** Anti-patterns with explanations are not filler. They are one of the most effective teaching mechanisms available.

### Ambiguity causes inconsistency

When a skill says "consider using X" or "you might want to Y" or "it's generally better to Z", I have to make a judgment call each time. Sometimes I'll do it, sometimes I won't, depending on the surrounding context. This creates the frustrating experience where Claude "sometimes gets it right."

**Implication:** Eliminate hedging language. If it's a rule, state it as a rule. If it's conditional, state the exact condition.

---

## The Anatomy of an Effective Skill

### 1. Identity: What Is This and When Does It Activate?

Every skill needs to answer two questions immediately:

1. **What domain does this cover?** (authentication, caching, forms, testing, etc.)
2. **What triggers should cause this skill to load?** (creating an entity, modifying a template, fixing a bug, etc.)

The trigger conditions are arguably more important than the content itself, because a perfectly written skill that never loads is worthless. Triggers should be specific, observable actions — not abstract concepts.

**Effective triggers:**
- "When creating or modifying a database entity"
- "When adding a Redis publisher to a service"
- "When the user asks to fix a frontend bug with a Jira reference"

**Ineffective triggers:**
- "When working on the backend" (too broad — always active, dilutes attention)
- "When relevant" (meaningless — everything is relevant to something)
- "For best practices" (vague — best practices for what?)

### 2. The Prime Directive: What Must I Do?

The first thing after the title should be the **single most important behavioral instruction** — the one thing that, if I do nothing else from this skill, I must do this.

Write it in bold. Make it imperative. Make it unambiguous.

**Good prime directives:**
- "Never expose internal numeric `id` fields in any external-facing interface. Use `publicId` for all cross-service references."
- "Before writing any fix, explicitly identify both the easy fix and the correct fix. Always choose the correct fix."
- "Always find a working example in another service and copy the exact pattern. Do not invent new approaches."

**Poor prime directives:**
- "This skill covers the authentication system." (descriptive, not directive)
- "Consider the following guidelines when working with the API." (hedging, not commanding)
- "It is important to be careful with database migrations." (vague, no actionable instruction)

### 3. The Rules: What Are the Invariants?

After the prime directive, list the concrete rules. These are the invariants I must maintain — things that are always true regardless of context.

Rules work best as **numbered or bulleted lists** with **bold lead-ins**:

```markdown
1. **Always use `registerAsync`, never `register`** — dynamic config requires async factory patterns
2. **Never share Redis subscriber and publisher connections** — subscriber enters a mode that blocks commands
3. **Return `false` from sync methods when data is empty** — throwing triggers process termination
```

Each rule should be one sentence (the rule) optionally followed by one sentence (the reason). If a rule needs more than two sentences to explain, it's probably a section, not a rule.

**How many rules?** Aim for 5-12. Fewer than 5 and the skill probably doesn't warrant being a skill. More than 12 and I start losing track — prioritize and merge.

### 4. The How: Procedures and Code

For skills that involve implementation (not just conventions), provide step-by-step procedures with code. Here is what makes code examples effective versus wasteful:

**What makes a code example effective:**

- **Complete** — includes imports, decorators, class definition, constructor injection, the actual logic, and exports. I can reproduce this verbatim and it will compile.
- **Annotated** — inline comments explain *why*, not *what*. I can read the code to see what it does; I need comments to understand the reasoning.
- **Contextual** — shows the file path where this code should live, and ideally the surrounding module registration.
- **Copy-ready** — uses real types from the project, real injection tokens, real module names. Placeholders are fine for service-specific values (`{SERVICE_NAME}`), but the structural code should be real.

**What makes a code example wasteful:**

- **Fragments** — a 3-line snippet without imports or class context. I have to guess the rest and may guess wrong.
- **Pseudocode without a real example alongside it** — pseudocode is fine for explaining logic, but I need at least one complete real example to anchor the pattern.
- **Multiple examples showing the same thing** — one complete example teaches the pattern. Three examples showing slight variations just consume tokens without adding information.
- **Examples from generic documentation** — code that uses generic names like `MyService`, `handleData()`, `processItem()` without being tied to the actual project domain. I already know generic NestJS/Angular patterns from training — what I need from a skill is *your project's specific conventions*.

### 5. The Boundaries: What Must I Avoid?

Anti-patterns are not a "nice to have" section. They are one of the most effective tools in a skill because they create **strong negative anchors** in my reasoning.

The most effective anti-pattern format:

```markdown
| What you might be tempted to do | Why it fails | What to do instead |
|---|---|---|
| [The attractive wrong approach] | [Specific failure mode — error message, runtime crash, data corruption] | [The correct approach, cross-referencing the main procedure] |
```

What makes anti-patterns effective:

- **They describe the temptation** — "You might think X is fine because..." This mirrors my own reasoning process and catches me before I go down that path.
- **They name the specific failure** — "causes `UnknownDependenciesException` at runtime" is far more memorable than "doesn't work correctly."
- **They link back to the correct approach** — don't just say "don't do X", redirect to the right answer.

### 6. Edge Cases and Decision Trees

Real-world tasks don't always fit neatly into the happy path. A good skill anticipates the common deviations and provides explicit decision logic.

**Decision tables** are the most efficient format for this:

```markdown
| Situation | Action |
|---|---|
| Entity is only used within one service | No publicId needed — internal only |
| Entity is referenced in gRPC responses | Must have publicId |
| Entity existed before this convention | Add publicId in a migration, update all consumers |
```

This is vastly more effective than prose descriptions of the same logic, because:
- I can scan a table quickly without parsing paragraphs
- The structure forces completeness — empty cells reveal gaps
- It's unambiguous — each row is a concrete scenario with a concrete action

### 7. Verification: How Do I Know I Did It Right?

After following a skill, I need to know whether my implementation is correct. The best skills include one or more of:

- **Build/lint/test commands** to run
- **Expected behavior** ("after this change, X should return Y")
- **What to check** ("verify the module exports array includes the new service")
- **A checklist** for self-review before considering the task done

Without verification criteria, I report the task as done based on my own judgment, which can miss project-specific requirements.

---

## Structural Principles

### Depth Calibration: How Long Should a Skill Be?

The right length is determined by the **density of project-specific information** — things I cannot derive from my training data or from reading the code.

| Content Type | Value to Skill | Action |
|---|---|---|
| Project-specific patterns I can't infer | **High** | Include with full detail |
| Non-obvious gotchas that cause runtime failures | **High** | Include with error messages |
| Configuration that deviates from framework defaults | **High** | Include exact values |
| Standard framework usage matching the docs | **Low** | Omit — I know this from training |
| General programming principles | **Zero** | Omit entirely |
| Explanations of how the framework works | **Low** | Omit unless your usage is non-standard |

A skill about "how to use TypeORM" that repeats TypeORM documentation is wasteful. A skill about "how *we* use TypeORM — our naming strategy, our base repository, our migration workflow, our entity conventions" is high-value.

### Information Hierarchy: What Goes Where?

Structure information in order of **how often it's needed** and **how critical it is**:

```
1. Prime directive (always needed, always critical)
2. Core rules (always needed, high criticality)
3. Step-by-step procedure (needed for implementation tasks)
4. Decision trees / edge cases (needed when the happy path doesn't apply)
5. Anti-patterns (needed during implementation, acts as guardrails)
6. Troubleshooting (needed when something goes wrong)
7. References / cross-links (needed for deeper exploration)
```

This ordering maps to my attention pattern — the most critical items get the strongest attention weight.

### Splitting: When One File Becomes Many

A single SKILL.md works well up to roughly **600-800 lines**. Beyond that, cognitive load increases and later sections get less attention weight.

**Split when:**
- The skill covers multiple distinct sub-domains that are independently useful
- A developer would typically only need one section per task, not the whole skill
- Code examples are so extensive they dwarf the rules they support

**How to split:**
- Keep SKILL.md as the **entry point** — prime directive, core rules, and links to sub-files
- Each sub-file should be **self-contained** — readable without reading the parent
- Name sub-files by topic, not by number (`architecture.md` not `part-2.md`)

**Keep together when:**
- Sections reference each other heavily
- The skill is a linear procedure where skipping steps causes errors
- The total length is manageable (under 600 lines)

### Reference Files: Separating Rules from Code

When a skill needs extensive code examples (100+ lines of implementation templates), separate them:

- **SKILL.md** — rules, procedures, decisions, anti-patterns (the "what and why")
- **reference.md** — complete code templates, full module definitions, copy-paste implementations (the "how, in detail")

This separation keeps the rules document scannable while making the full implementation available when needed. I will read the reference file when I need to write code; I will read SKILL.md when I need to make decisions.

---

## Writing Style That Maximizes Compliance

### Imperative Voice

Every instruction should be a command. Compare:

| Passive/Descriptive | Imperative |
|---|---|
| "The `publicId` field is typically added to entities" | "Add a `publicId` field to every entity that is referenced externally" |
| "It's recommended to use `for...of` instead of `forEach`" | "Never use `forEach` for async iteration. Use `for...of`" |
| "The subscriber client should generally be used" | "Always inject `REDIS_SUB_CLIENT`. Never use the publisher client for subscriptions" |

I comply more reliably with imperatives because there's no ambiguity about whether the instruction applies to the current situation.

### Absolute vs. Conditional Rules

**Absolute rules** are the strongest instructions. Use them when the rule truly has no exceptions:
- "Never expose internal numeric IDs in external APIs"
- "Always use `registerAsync` for module registration"

**Conditional rules** are necessary when the action depends on context. State the condition first, then the action:
- "When the entity is only used within one service, skip `publicId`"
- "If the service is both publisher and consumer, use separate Redis connections"

Avoid conditions that require subjective judgment:
- "If it seems appropriate..." (appropriate by what criteria?)
- "When it makes sense to..." (it always makes sense from some angle)
- "For complex cases..." (what threshold defines complex?)

### Naming Rules for Memorability

When you have many rules, naming them makes them easier for me to reference and for users to discuss:

```markdown
1. **The Subscriber Rule** — never share subscriber and publisher Redis connections
2. **The Sync-or-Die Rule** — `syncLoadConfiguration` returning false 5 times kills the process
3. **The Copy Rule** — always find a working service and copy its pattern exactly
```

Named rules also help in correction: "You violated the Copy Rule" is clearer than "you wired the module wrong."

### Quantify Wherever Possible

Vague guidance creates inconsistency. Specific numbers create consistency:

| Vague | Specific |
|---|---|
| "Keep functions short" | "Functions over 50 lines should be split" |
| "Use a reasonable retry count" | "Retry up to 5 times with 1-second intervals" |
| "Don't make skills too long" | "Split skills exceeding 800 lines into sub-files" |
| "Wait for Redis to be ready" | "Retry Redis connection every 2000ms, max 30 retries" |

---

## What Makes Skills Fail: Root Causes

These are the failure modes I observe most often when skills don't produce the intended behavior:

### 1. The Skill Describes Instead of Prescribing

**Symptom:** I produce code that is technically valid but doesn't follow the project's specific pattern.

**Root cause:** The skill reads like documentation ("AbCache is a caching system that uses Redis pub/sub to...") instead of instructions ("Register AbCache with `registerAsync`. Pass `REDIS_SUB_CLIENT`. Set `autoSync: true` for...").

**Fix:** Rewrite every paragraph as an instruction. If a sentence doesn't tell me to do something, question whether it belongs.

### 2. The Skill Has No Complete Code Example

**Symptom:** I write code that compiles but uses wrong imports, wrong injection tokens, wrong class names, or wrong module structure.

**Root cause:** The skill explains the concept but relies on me to figure out the implementation from my training data. My training data knows generic framework patterns, not your project's specific conventions.

**Fix:** Include at least one complete, compilable example per major pattern. "Complete" means: imports, class definition, constructor, method body, and module registration.

### 3. The Skill Is Too Long and Unfocused

**Symptom:** I follow some rules from the skill but miss others, especially those in later sections.

**Root cause:** A 2000-line skill with mixed concerns dilutes the attention I can give to any single rule. The rules on line 1800 get less weight than the rules on line 50.

**Fix:** Split into focused sub-files. Put the highest-priority rules at the top. Remove content I can derive from training data or from reading the code.

### 4. The Skill Has Ambiguous Conditions

**Symptom:** I apply a rule inconsistently — sometimes I do it, sometimes I don't.

**Root cause:** The skill uses hedging language ("consider", "generally", "when appropriate") or conditions that require subjective judgment.

**Fix:** Replace every "consider doing X" with either "always do X" or "do X when [specific, observable condition]."

### 5. The Skill Contradicts Another Skill

**Symptom:** My behavior oscillates or I produce hybrid approaches that satisfy neither skill fully.

**Root cause:** Two skills give conflicting instructions for the same scenario, or one skill's rules have implicit exceptions that another skill doesn't acknowledge.

**Fix:** Cross-reference skills. If two skills cover overlapping territory, one should explicitly defer to the other for the overlapping concern: "For database entity conventions, follow `database-implementation-principles` — this skill only covers the API layer."

### 6. The Skill Lacks Negative Examples

**Symptom:** I produce the "obvious" approach (which happens to be wrong for this project) instead of the project-specific correct approach.

**Root cause:** The skill shows what to do but not what to avoid. The "obvious" approach from my training data competes with the skill's instructions, and without an explicit "never do this" anchor, the training-data pattern sometimes wins.

**Fix:** Add an anti-patterns section. For every non-obvious rule, show the tempting wrong approach, explain why it fails in this project specifically, and redirect to the correct approach.

### 7. The Skill Has No Verification Step

**Symptom:** I report the task as complete when it's actually broken in ways that would be caught by a build/test/lint cycle.

**Root cause:** Without explicit verification instructions, I rely on my own judgment about completeness, which can miss project-specific requirements (lint rules, test coverage expectations, build configurations).

**Fix:** End every implementation skill with a verification section: what commands to run, what output to expect, what to check manually.

---

## Skill Categories and Their Priorities

Different types of skills need different emphasis. Here is what matters most for each:

### Convention / Style Skills

**Priority order:** Rules > Anti-patterns > Examples > Reasoning

Convention skills are about compliance. I need to know what to do and what not to do. I don't necessarily need to understand the full history of why the convention was adopted — that's context for humans, not execution guidance for me.

### Implementation / Infrastructure Skills

**Priority order:** Code examples > Procedures > Constraints > Troubleshooting

Implementation skills are about reproducing a pattern correctly. The code example is the anchor; everything else supports it. A 50-line working code block teaches me more than 200 lines of prose about the same topic.

### Workflow Skills

**Priority order:** Sequence > Decision points > Templates > Anti-patterns

Workflow skills are about doing things in the right order. I need to know: what are the phases, what triggers movement between phases, and what artifacts are produced at each phase.

### Decision Framework Skills

**Priority order:** Decision criteria > Examples > Red flags > Edge cases

Decision skills are about choosing correctly. The decision table or framework is the core artifact. Real examples ground the abstract criteria in concrete scenarios.

---

## The Skill Quality Test

Before considering a skill complete, run it through these questions:

1. **If I deleted all the prose and kept only the rules and code, would the skill still work?** If yes, the prose is probably filler. If no, the prose is carrying essential information that should be restructured as rules or examples.

2. **Could I follow this skill correctly on my first encounter with this codebase?** If the skill assumes knowledge it doesn't provide and doesn't point to where that knowledge lives, it has gaps.

3. **Is every rule testable?** Can you look at my output and definitively say "this violates rule N" or "this complies with rule N"? If a rule is too vague to test, it's too vague to follow consistently.

4. **Does the skill tell me what to do when the happy path doesn't apply?** Most skills are written for the 80% case. The 20% edge cases are where I'm most likely to produce wrong output — those are the situations that most need explicit guidance.

5. **Could this skill be half as long without losing information?** If yes, cut it. Every unnecessary token competes with the actual task for attention weight.

6. **Does the skill contain information I already know from training?** Standard framework documentation, general programming principles, and well-known patterns are in my training data. Skills should cover the **delta** — what's different or non-obvious about *your* project.

7. **Are the anti-patterns specific enough?** "Don't write bad code" is useless. "Don't use `useClass` for `HttpXRequestIdInterceptor` because the optional constructor parameter causes `UnknownDependenciesException`" is immediately actionable.

8. **Would two different people reading this skill write the same instructions?** If the skill is ambiguous enough that reasonable people would interpret it differently, I will also interpret it differently across contexts.

---

## Token Economics: The Cost-Benefit of Skill Content

Every section of a skill has a cost (tokens consumed from the context window) and a benefit (improvement in output quality). Here is how different content types rank:

| Content Type | Token Cost | Quality Impact | ROI |
|---|---|---|---|
| A complete, working code example | Medium (50-100 lines) | Very High | **Excellent** |
| An anti-pattern with specific error message | Low (3-5 lines) | High | **Excellent** |
| A decision table (5-10 rows) | Low (10-20 lines) | High | **Excellent** |
| A clear imperative rule | Very Low (1-2 lines) | High | **Excellent** |
| A numbered procedure (5-10 steps) | Low (15-30 lines) | Medium-High | **Good** |
| A troubleshooting table | Low (10-15 lines) | Medium (only relevant when things fail) | **Good** |
| Explanation of why a rule exists | Low (3-5 lines) | Medium | **Good** |
| Background/history of the system | Medium (20-50 lines) | Low | **Poor** |
| Repetition of standard framework docs | Medium (30-100 lines) | Zero | **Wasteful** |
| Verbose prose restating what code shows | Medium (20-50 lines) | Negative (dilutes focus) | **Harmful** |

Optimize for the top half. Eliminate the bottom two.

---

## Summary: What I Need from a Skill

1. **Tell me what to do first, explain why after.** Instruction, then reasoning — never the reverse.
2. **Show me one complete, working example.** Not fragments, not pseudocode alone, not generic tutorial code — a real, compilable example from the project.
3. **Show me what to avoid, and name the specific failure.** "Don't do X because it causes error Y" is one of the most valuable things a skill can contain.
4. **Be specific and absolute wherever possible.** "Always" and "never" produce consistency. "Consider" and "generally" produce inconsistency.
5. **Respect my context window.** Every token spent on generic framework knowledge or verbose prose is a token not available for the actual task. Be ruthlessly concise.
6. **Cover the edge cases.** The happy path is where I need skills least. The weird scenarios are where I need them most.
7. **Give me verification criteria.** Tell me how to confirm my implementation is correct before reporting it as done.
8. **Don't tell me what I already know.** Skills should encode the **delta** between standard framework usage and your project's specific conventions. That delta is the entire value of the skill.

---

## Example: Ideal SKILL.md

This is a complete, model skill demonstrating every required section at the right depth. Use it as a template.

````markdown
---
name: redis-pub-sub
description: >-
  Wiring Redis pub/sub in NestJS microservices. Use when adding a Redis publisher
  or subscriber to any service, or when registering RedisModule in a new module.
---

# Redis Pub/Sub — Service Wiring

**Always use separate Redis clients for publishing and subscribing. Never share a single connection for both roles.**

## Rules

1. **Two clients, always** — register `REDIS_PUB_CLIENT` and `REDIS_SUB_CLIENT` as separate providers. A subscriber connection enters a special mode that blocks normal commands.
2. **Channel naming** — `{owner-service}:{grpc-method-slug}:update` (e.g., `bot-position-service:get-positions:update`).
3. **Use `registerAsync`** — inject `ConfigService` for host/port. Never hardcode connection details.
4. **Unsubscribe on module destroy** — implement `OnModuleDestroy` and call `client.unsubscribe()` to prevent connection leaks.

## Procedure

### 1. Register the module

```typescript
// src/redis/redis.module.ts
import { Module } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import Redis from 'ioredis';

@Module({
  providers: [
    {
      provide: 'REDIS_PUB_CLIENT',
      useFactory: (config: ConfigService) =>
        new Redis({ host: config.get('REDIS_HOST'), port: config.get('REDIS_PORT') }),
      inject: [ConfigService],
    },
    {
      provide: 'REDIS_SUB_CLIENT',
      useFactory: (config: ConfigService) =>
        new Redis({ host: config.get('REDIS_HOST'), port: config.get('REDIS_PORT') }),
      inject: [ConfigService],
    },
  ],
  exports: ['REDIS_PUB_CLIENT', 'REDIS_SUB_CLIENT'],
})
export class RedisModule {}
```

### 2. Inject and use

```typescript
// src/position/position-update.service.ts
import { Inject, Injectable, OnModuleDestroy } from '@nestjs/common';
import Redis from 'ioredis';

@Injectable()
export class PositionUpdateService implements OnModuleDestroy {
  constructor(
    @Inject('REDIS_PUB_CLIENT') private readonly pub: Redis,
    @Inject('REDIS_SUB_CLIENT') private readonly sub: Redis,
  ) {}

  /** Publishes a position snapshot to all listening gateways. */
  async publishUpdate(channel: string, payload: string): Promise<void> {
    await this.pub.publish(channel, payload);
  }

  onModuleDestroy(): void {
    // Prevent connection leaks when the module is torn down
    this.sub.unsubscribe();
  }
}
```

## Anti-Patterns

| Temptation | Why it fails | Do this instead |
|---|---|---|
| Single shared Redis client for pub + sub | Subscriber enters subscribe mode, blocking all other commands — `publish()` throws | Use two separate clients (Rule 1) |
| `useClass: RedisService` with `@Injectable()` | Cannot pass config to constructor — hardcodes `localhost:6379` | Use `useFactory` with `ConfigService` (Rule 3) |
| Forgetting `onModuleDestroy` cleanup | Connections accumulate across hot-reloads in dev, exhaust Redis `maxclients` in prod | Implement `OnModuleDestroy` (Rule 4) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERR only (P)SUBSCRIBE / (P)UNSUBSCRIBE / PING / QUIT allowed` | Publishing on the subscriber client | Verify you injected `REDIS_PUB_CLIENT`, not `REDIS_SUB_CLIENT` |
| Messages not received after deploy | Channel name mismatch between publisher and subscriber | Check both sides use the exact `{service}:{method}:update` format |
| `MaxListenersExceededWarning` in dev | No `onModuleDestroy` — hot-reload stacks connections | Add cleanup (Rule 4) |

## Verification

1. Run `npm run build` — no compile errors
2. Run `npm run lint` — no formatting issues
3. Start the service — confirm logs show two Redis connections established
4. Publish a test message — confirm the subscriber receives it

## See Also

- `skills/ab-cache` — higher-level caching that uses Redis pub/sub internally
- `skills/copy-from-working-service` — find an existing service with Redis wiring and replicate
- `skills/resilience` — multi-pod considerations for Redis subscribers
````

---

## Frontmatter Template

```yaml
---
name: kebab-case-skill-name
description: >-
  One to three sentences. Must answer: (1) what domain does this cover,
  (2) what specific actions trigger this skill. Be precise about triggers —
  vague descriptions ("helps with backend") never match.
---
```

**Trigger-clause forms.** Both singular (`Trigger when` / `Trigger on` / `Use when` / `USER-INVOKED` / `DO NOT auto-load`) and plural (`Triggers when` / `Triggers on`) are accepted. Pick whichever reads more naturally — singular is fine for one condition; plural is natural English when the description lists multiple. Audit and verification tooling MUST grep for both forms (canonical regex: `(Use when|USER-INVOKED|DO NOT auto-load|Trigger when|Trigger on|Triggers when|Triggers on)`).

---

## Edit vs Judge Separation

**The agent that edits a skill must NOT be the agent that scores it.** When upgrading multiple skills:

1. **Implementation agent** reads the current SKILL.md, writes the audit doc, and edits the skill
2. **Reviewer agent** (separate, fresh context) re-scores the edited skill against this rubric and writes the final score into the audit doc
3. If the reviewer scores below A or any floor criterion fails, the implementation agent revises

This prevents self-grading bias. A single developer editing one skill can self-review, but batch operations across 10+ skills require the split.

---

