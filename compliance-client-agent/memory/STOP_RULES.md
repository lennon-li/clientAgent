# compliance — Stop Rules

These project-specific rules supplement `AgentSystem/agent-memory/policies/STOP_RULES.md`.

## Always stop before

- Deploying anything to `/srv/shiny-server/compliance`, or restarting
  `shiny-server`. Deployment requires `sudo` and is Lennon's decision.
- Pushing to `origin`, opening a PR, or merging into `main`.
- Opening, copying, or moving anything under `backup/`, `output/`, or
  `library/`. `backup/data/people.rds` holds real employee data.
- Deleting or rewriting the legacy root-level `app*.R` files, which are
  historical copies that have not been triaged.
- Changing the compliance classification logic in `R/getComplianceRate.R` or
  `R/compliance_rulebook.R` without Lennon confirming the rule change, since
  these determine reported attendance outcomes for real staff.
