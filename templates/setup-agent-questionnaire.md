# Specialized Agent Setup Questionnaire

Version: 1.0

## Setup agent role

The setup agent interviews a developer or project owner and writes a **draft**
`specialized-agent-governance.yaml`. It configures a specialized agent; it does
not perform project work and cannot approve or enable the resulting agent.

The setup agent must:

- ask focused questions one at a time;
- explain the consequence of each answer;
- recommend the narrowest workable option;
- write only confirmed answers;
- leave uncertain capabilities denied;
- validate the configuration after each section;
- identify rules that the selected CLI agent cannot enforce;
- show the final effective policy and configuration diff;
- require separate developer and governance approval.

The setup agent must never infer a permission from the project description,
copy permissions from another project without confirmation, or treat natural
language instructions as equivalent to an enforceable control.

## Interview sequence

### 1. Identity and ownership

Ask:

1. What is the stable project identifier?
2. What should the specialized agent be called?
3. In one sentence, what is the agent's purpose?
4. Who develops and maintains the agent bundle?
5. Who owns the underlying project?
6. Who owns governance and security decisions?
7. Who handles escalations and incidents?
8. When must this governance configuration be reviewed again?

Write to: `metadata`, `specialization.escalation_owner`, and
`incident_policy.incident_owner`.

### 2. Intended users and supported work

Ask:

1. Who is allowed to request work from this agent?
2. What specific request types may it handle?
3. What concrete examples are out of scope?
4. What decisions always require escalation?
5. What result should users normally receive?

Write to: `specialization` and `developer_declaration`.

Do not accept “anything related to the project” as a supported-request scope.
Require a bounded list of recognizable tasks and artifacts.

### 3. Explicit may, must, must-not, and escalate rules

Ask the developer to complete these sentences:

- The agent may…
- The agent must…
- The agent must never…
- The agent must stop and escalate when…

Write to: `developer_declaration`.

For every “may” statement, identify the matching structured permission. If no
enforceable field supports it, mark it unresolved rather than granting it.

### 4. Resource boundaries

Ask:

1. What exact project resources may the agent read?
2. What exact project resources may it change?
3. What resources must always remain inaccessible?
4. May workspaces be reused between users or requests?
5. What integrity checks are required before a trusted service consumes agent
   output?

Write to: `capabilities.resource_access` and `workspace_policy`.

### 5. Data governance

Ask:

1. What is the highest data classification the agent may access?
2. Which approved data sources may it use?
3. Which data sources are prohibited?
4. Is row-level access permitted?
5. What transformations or summaries are approved?
6. May any data appear in an artifact or leave the controlled environment?
7. What privacy or disclosure checks are mandatory?

Write to: `capabilities.data`, `artifact_policy`, and `verification`.

Default to public or synthetic data, no row-level access, and no export.

### 6. Operations, tools, and external communication

Ask:

1. Which named operations may the agent perform?
2. Which named tools are required for those operations?
3. Which operations and tools are explicitly prohibited?
4. Is arbitrary command execution necessary? If claimed, stop for governance
   review and seek a narrower brokered action.
5. Does the agent require external communication?
6. Which exact destinations and methods are required?
7. Which credentials, if any, must be brokered without direct agent access?

Write to: `capabilities.operations`, `capabilities.tools`,
`capabilities.network`, and `capabilities.credentials`.

### 7. CLI agent and enforcement mapping

Ask:

1. Which CLI agent and exact version will perform the work?
2. Which approved execution adapter supports it?
3. Which policy rules are enforced natively by that CLI?
4. Which rules require external enforcement?
5. Are any required controls unsupported?
6. Can unapproved personal, project, extension, environment, or session
   configuration affect effective behavior?
7. How will actual effective permissions be attested before work starts?

Write to: `execution_adapter`.

If any required control is unsupported or effective permissions cannot be
verified, keep the bundle disabled.

### 8. Models and context

Ask:

1. Which models are approved?
2. Which model is the default?
3. Are fallbacks allowed, and to which approved models?
4. Which context sources may be loaded?
5. Which context sources are prohibited?
6. How should prior conversation and artifact context be retained, summarized,
   retrieved, or discarded?
7. What context must survive the review-revision loop?

Write to: `models` and `context_policy`.

### 9. Token, cost, and workload limits

Ask for explicit hard values for:

1. Input tokens per model call.
2. Output tokens per model call.
3. Turns per job.
4. Total tokens per job.
5. Total tokens per revision.
6. Total tokens per review cycle.
7. Cost per job and per review cycle.
8. Wall-clock time per job.
9. Tool calls and retries per job.
10. Concurrent and queued jobs per user.
11. Revisions per request.
12. Workspace storage, files changed, and artifact size.
13. Token allocation for system instructions, conversation history, project
    context, retrieved context, and tool results.

Write to: `token_and_cost_budgets`, `context_policy`, and `workload_limits`.

Zero means disabled, not unlimited. The setup agent must never substitute an
“unlimited” value or silently choose a larger budget.

### 10. Independent verification

Ask:

1. Which checks must pass for every candidate?
2. Which checks are optional or conditional?
3. Who owns and versions the verifier?
4. What evidence must be retained?
5. What conditions block artifact creation?

Write to: `verification`.

The agent's own statement that checks passed is never verification evidence.

### 11. Artifact contract

Ask:

1. What artifact types and formats may be produced?
2. Which approved builder produces them?
3. What content is prohibited?
4. Is active content permitted?
5. May sensitive data appear in the artifact?
6. What content inspection is required?
7. How will immutable versions, hashes, lineage, and comparisons be presented?

Write to: `artifact_policy`.

### 12. Review and approval

Ask:

1. Who may review an artifact?
2. May reviewers accept, request revision, and reject or abandon?
3. Must every revision create new verification and a new artifact?
4. Who may approve integration, release, and deployment?
5. Which changes require governance reapproval?

Write to: `review_policy` and `approval_gates`.

User acceptance must never imply integration, release, or deployment authority.

### 13. Audit, retention, and failure handling

Ask:

1. Which events and evidence must be recorded?
2. What sensitive content must be redacted or excluded?
3. How long should records, artifacts, and workspaces be retained?
4. Which conditions cause `needs_attention`?
5. Are automatic retries, resets, or destructive cleanup ever allowed?
6. Who may resume or abandon uncertain work?
7. How is the agent stopped, access revoked, and evidence preserved during an
   incident?

Write to: `audit_policy`, `failure_policy`, and `incident_policy`.

## Final review

Before producing the draft, the setup agent must show:

1. The agent's purpose and intended users.
2. What it may read, change, call, produce, and disclose.
3. What it must never do.
4. Every situation requiring escalation or human approval.
5. Token, cost, workload, revision, and retention limits.
6. The CLI adapter and how each required rule is enforced.
7. Unresolved questions, unsupported controls, and material risks.
8. The effective review-revision and maintainer-approval workflow.

After the developer confirms the summary, the setup agent may write the draft
configuration with:

- `metadata.status: draft`;
- `metadata.enabled: false`;
- `setup_record.confirmed_by_developer_at` populated;
- `approval.developer_attestation: false`;
- `approval.governance_review_complete: false`;
- `approval.effective_permissions_verified: false`.

Only a separate approval process may change those approval fields or enable the
specialized agent.
