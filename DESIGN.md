# Tower Pilot dashboard design
> **Status:** Active
> **Type:** Design reference
> **Created:** 2026-10-12
> **Updated:** 2026-10-23
> **Scope:** Where the dashboard's design system and page rules live

The dashboard is the app in [frontend/app/](frontend/app/), served at `/ui/app/`.
The previous dashboard (`frontend/webui/index.html` panels, `classic.html` and
their modules) was retired by plan R17; `/ui/index.html` is now only a redirect
that opens the matching app page for an old bookmark.

| Concern | Owner |
| --- | --- |
| Colour, spacing, type and status tokens | [src/ui/tokens.css](frontend/app/src/ui/tokens.css); components use these variables only |
| Shared components (Button, Chip, Notice, ConfirmDialog, Disclosure, ...) | [src/ui/](frontend/app/src/ui/) |
| Page ids and links between pages | [src/ui/routes.ts](frontend/app/src/ui/routes.ts) |
| Every user-visible string, as `{short, why?}` | the copy catalogue, [src/copy/](frontend/app/src/copy/) ([catalogue.ts](frontend/app/src/copy/catalogue.ts)) |
| Layers and which may import which | [frontend/app/README.md](frontend/app/README.md) |
| Page behaviour: what each Dashboard region shows and why | the Dashboard spec, `backend/logs/reviews/ui-dashboard-rebuild/dashboard-spec.md` (a local review record, git-ignored), and one brief per page beside it |

Setting ownership and scope: [configuration guide](docs/SETTINGS.md). Learning
acceptance and reconciliation: [learning contract](docs/LEARNING_FLOWS.md).
Purchase semantics and safety contracts: [run lifecycle plan](docs/RUN_LIFECYCLE_PLAN.md).
Screen captures, learned images and account data remain local under the
[project rules](CLAUDE.md#image-free-release-boundary).
