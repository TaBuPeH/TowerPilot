# Tower Pilot dashboard app
> **Status:** Active (Phase 0 scaffold)
> **Type:** Knowledge
> **Created:** 2026-10-21
> **Updated:** 2026-10-29
> **Decision record:** ADR-001, frontend stack and structure (2026-10-21)

## Purpose

The new dashboard application: React 19, TypeScript (strict), Vite. It builds into
`frontend/webui/app/`, which Flask serves at `/ui/app/`. The build is git-ignored;
CI and the release scripts produce it. It is the only dashboard: `/` serves it,
and `/ui/index.html` is a small redirect page that maps the old page anchors
to the app's pages (R17).

---

## Commands

Node.js 22.13+ (22.x), 24.x or 26+ (`engines` in package.json; the locked dependencies accept nothing else). Run from this folder.

| Command             | What it does                                                                      |
| ------------------- | --------------------------------------------------------------------------------- |
| `npm ci`            | Install the exact versions in `package-lock.json`                                 |
| `npm run dev`       | Dev server on `http://127.0.0.1:5173/ui/app/` with the fixture-based fake backend |
| `npm run build`     | Production build into `../webui/app/` with `THIRD_PARTY_NOTICES.txt`              |
| `npm run typecheck` | `tsc` over the app, tests, tooling and the DOM-free `core/`                       |
| `npm run lint`      | ESLint (layers, no literals, a11y, hooks, typed rules) and Prettier check, on Node's default heap. ESLint runs twice, `src` then everything else, so each typed-rule run loads fewer files (peaks about 3.1 GB each of the 4.3 GB default, measured 2026-10-22; one run over the whole tree had reached 3.9 GB). The generator exports a named type only for each family root |
| `npm test`          | Vitest: unit, component (Testing Library, jest-axe) and type tests                |
| `npm run e2e`       | Playwright against the built app and the fake backend (build first)               |
| `npm run format`    | Prettier write                                                                    |

`npm run dev` never talks to a real dashboard unless asked:
`TOWER_PILOT_DEV_BACKEND=http://127.0.0.1:<port>` proxies `/api` to a dashboard on
this machine and forwards GET and HEAD only.

---

## Layers

| Folder          | Holds                                                                 |
| --------------- | --------------------------------------------------------------------- |
| `src/core/`     | Evidence rules and projections, pure TypeScript (`Fact`, `Confirmed`) |
| `src/contract/` | Endpoint types and zod schemas, checked against the backend fixtures  |
| `src/api/`      | The only code that talks to the server                                |
| `src/store/`    | Store and selectors (`useSyncExternalStore`)                          |
| `src/copy/`     | Every user-visible string, as `{short, why?}`                         |
| `src/ui/`       | Design system: tokens, `Chip`, `Button`, `CopyLine`, `Why`            |
| `src/features/` | One folder per page                                                   |
| `src/app/`      | Shell, hash router, error boundary, live region, wiring               |

Which layer may import which is defined once, in `MAY_IMPORT` in
[eslint-rules/policy.js](eslint-rules/policy.js); only `src/api/` and `src/store/`
import `src/contract/` (`store/` imports `api/` for types only), and components
read `store/` view models. The lint rules are guardrails against honest mistakes,
not a security boundary (ADR-001 Amendment 1, trimmed in F1): layer zones, no JSX
text and no plain-`string` JSX child (`react/jsx-no-literals` and
[eslint-rules/no-string-jsx-child.js](eslint-rules/no-string-jsx-child.js)), no type
assertions outside the homes of the type-only brands, the network only from
`src/api/`, and no `eval`, `new Function` or `dangerouslySetInnerHTML`. The
render check in [tests/unit/copy/renderedText.ts](tests/unit/copy/renderedText.ts) backs
this up at runtime, and [tests/unit/lint/rules.test.ts](tests/unit/lint/rules.test.ts) holds
one probe per rule.

The ADR-001 decision record is kept with the project's review logs, which are not
part of the repository, so it is named here rather than linked.

### Application shell and dashboard modules

`app/AppNavigation.tsx` owns the persistent desktop sidebar and the bounded inline
navigation panel on narrow screens. It uses one destination inventory from the
copy catalogue and the existing `ui/routes.ts` hash contract. Navigation sits
outside the routed-page error boundary; each page still owns its header and main
landmark. `app/appShell.css` exposes the remaining `workspace` width as a CSS
container, so feature layouts account for the sidebar.

Dashboard modules keep one reading and keyboard order: Control, Needs you, This
battle, Start next, Plan and history, then Screen preview. Wide workspaces may
place the view-only preview beside the operations column. Controls, blockers and
unresolved outcomes remain visible; disclosure is reserved for secondary detail.
When Needs you has no issues, successful source checks and their individual ages
live under Checks and freshness; failed or unread checks stay visible beside the
summary. The summary reports the issue list, not overall system health.

The shared TopBar owns page identity, account/emulator context and connection
recovery. Dashboard's InputSlot owns its ordinary input-off action. Other pages
reuse `shared/InputControl.tsx` through `topBar/HeaderInputControl.tsx`, including
input request feedback through `outcomes/OutcomeReceipt.tsx`. Selectors and
commands continue to own eligibility, evidence and mutations. Header `readOnly`
suppresses connection actions; it does not suppress safe input revocation.

---

## Test layout

| Path | Purpose |
| --- | --- |
| `tests/unit/` | Vitest unit, component, DOM and type tests; setup and local helpers stay with their tests |
| `tests/e2e/` | Playwright browser specs, with browser helpers in `helpers/` and isolated configs in `configs/` |
| `tests/support/` | Recorded-case readers and test helpers shared by unit and browser tests |
| `tests/support/fake-backend/` | Fixture API server and scenario data used by Playwright and the dev server |
| `tests/fixtures/` | Recorded abilities, equipment and schedule-race samples |

Unit and browser tests share readers through `tests/support/`; support may import
`src/` and `scripts/`. The browser constraint tests also consume the generated
ledger in `tests/unit/spec/specData.ts`. The dev server imports the fake backend
through [scripts/dev-backend.ts](scripts/dev-backend.ts).

[playwright.config.ts](playwright.config.ts) stays at the app root for default
Playwright discovery. Isolated configs use
[one factory](tests/e2e/configs/isolated-config.ts) and retain their own ports,
builds and output directories. [tests/support/paths.ts](tests/support/paths.ts)
owns the app, source and repository roots used by test tooling.

---

## Notes

- ESLint 10 runs `eslint-plugin-react` 7.37.5 and `eslint-plugin-jsx-a11y` 6.10.2
  through `overrides` in `package.json`; both declare ESLint 9 at most. Remove the
  overrides when they declare ESLint 10. `eslint.config.js` explains the one
  workaround (React version read from the package, not detected).
- Contract tests read three backend fixtures in `backend/tests/fixtures/` (`harvest_status_v1.json`,
  `dashboard_contract_v1.json`, `dashboard_contract_pages_v1.json`) through
  `scripts/contract-fixture.mjs` and fail on drift.
