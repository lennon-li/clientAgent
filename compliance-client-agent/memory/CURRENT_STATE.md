# compliance — Current State

Last updated: 2026-09-01

Everything below was verified directly against the repository on `asgard` on
2026-08-18. No history is asserted that could not be read from the repo.

## Identity

- R package: `compliance` 0.0.0.9000
- Title: Workplace Compliance Shiny Application and Utilities
- Repo (asgard): `/home/yeli/shiny/compliance`
- GitHub: https://github.com/lennon-li/compliance.git
- Default branch: `main`
- Licence: `file LICENSE`

## Repo state as read on 2026-08-18

- Branch `main`, HEAD `1ec07d4` "Restructure compliance app as an R package"
- Only three commits exist: `ab0a8d0` init, `77e0fbb` tracking-record/AWA fix,
  `1ec07d4` package restructure
- Working tree clean apart from an uncommitted `AGENTS.md` added 2026-08-18

## Package surface

- `run_compliance_app()` launches the packaged Shiny app (`inst/app/app.R`)
- `compliance_rulebook()` documents the attendance-compliance logic
- `compliance_dependencies()` / `install_compliance_dependencies()` handle
  runtime package checks
- 22 files in `R/`, including `account2id.R`, `getComplianceRate.R`,
  `export_compliance_workbook.R`, `add_holiday.R`, `add_loa.R`, `addAWA.R`

## Tests

- testthat edition 3, `tests/testthat.R` calls `test_check("compliance")`
- Only one test file present: `tests/testthat/test-compliance-rulebook.R`
- Test command: `Rscript -e 'devtools::test()'`
- Coverage is therefore very thin; this is a known gap, not a verified pass rate

## Deployment

- Live app: `/srv/shiny-server/compliance`, served by `shiny-server` on :3838
- Client preview: the `/preview` prefix lets the trusted gateway publish only
  `inst/app/app.R` and `R/account2id.R` from the committed chat workspace to
  `/srv/shiny-server/test/compliance`. Jer cannot access `/srv` directly.
- Procedure is documented in the repo's `DEPLOYMENT.md`: `sudo cp` of `app.R`
  and `R/account2id.R` into the live directory, then
  `sudo systemctl restart shiny-server`, verified with
  `curl -I http://127.0.0.1:3838/compliance/` expecting 200
- Deployment requires root and is a maintainer action. Agents do not deploy.

## Data sensitivity

- `backup/`, `output/`, and `library/` are gitignored. `backup/data/people.rds`
  contains real employee data. These directories are never copied into agent
  worktrees, because `git worktree add` materialises tracked content only.

## Legacy files still in the repo root

`app.R`, `app-2026-04-21.R`, `appApril14.R`, `appold.R`, `appold1.R`, plus
built `compliance_0.0.0.9000.tar.gz` / `.zip`. The packaged app now lives at
`inst/app/app.R`; the root copies are historical and have not been pruned.

## Related systems

- Compliance client agent gateway: `/home/yeli/services/compliance-client-agent`
  (local-only MVP, built 2026-08-18). It works on a service-owned bare clone at
  `state/repo.git`, never on this checkout.
