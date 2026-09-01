# compliance — Memory Index

Last updated: 2026-08-19

Index of project memory for `compliance`. Canonical files follow the shared
convention documented in `projects/INDEX.md`.

> Convention note: sibling projects (`courieR`, `insider`) do not carry a
> `MEMORY_INDEX.md`; they rely on `projects/INDEX.md` plus the canonical file
> set. This file is added because the compliance client-agent gateway reads it
> as the agent entry point, and it indexes the same canonical set rather than
> replacing it.

## Canonical files

| File | Purpose |
|---|---|
| `CURRENT_STATE.md` | Dated head, verified repo facts, test surface |
| `BUILD_HISTORY.md` | Non-secret history and operating contract for Jer |
| `TODO.md` | Immediate work |
| `DECISIONS.md` | Durable decisions |
| `HANDOFF_LOG.md` | Dated handoffs |
| `STOP_RULES.md` | Project-specific stop rules |
| `AGENTS.md` | Ownership, loading protocol, tooling expectations |

## Identity (verified 2026-08-18 from the repo itself)

- R package `compliance`, version 0.0.0.9000, `Config/testthat/edition: 3`
- Repo: `/home/yeli/shiny/compliance`
- Remote: https://github.com/lennon-li/compliance.git
- Default branch: `main`
- Deployment target: `/srv/shiny-server/compliance`, served by `shiny-server`
- Test command: `Rscript -e 'devtools::test()'` (tests in `tests/testthat/`)

## Agent entry point

An agent picking up work on this project should read, in order:

1. This file
2. `CURRENT_STATE.md`
3. `BUILD_HISTORY.md`
4. `STOP_RULES.md`
5. `AGENTS.md` in the repository root (`/home/yeli/shiny/compliance/AGENTS.md`)
