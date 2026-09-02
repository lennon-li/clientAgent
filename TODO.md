# clientAgent — Product Definition and TODO

Last updated: 2026-09-02

## Slogan

> **Don't talk to me yet. Talk to my agent first.**

## What we are building

`clientAgent` is a framework for deploying **governed, project-specific AI
agents** that turn user requests into verified, reviewable artifacts through an
iterative review-revise loop, while keeping integration and release under human
control.

It is an **artifact-centered specialized-agent framework**. It is not a general
coding agent, a chatbot template, or an autonomous deployment system.

Each client-facing agent represents one project. It understands that project's
purpose, vocabulary, current state, approved workflows, expected artifacts, and
operating limits. It gives clients a conversational first point of contact
without giving them direct access to the project's implementation, sensitive
resources, or privileged operations.

The agent receives a request, determines whether it is within scope, asks only
the clarification needed for that project, performs bounded work, and returns a
concrete artifact for review. Depending on the project, the artifact could be a
document, report, analysis result, dashboard preview, design, configuration, or
structured change proposal. The artifact—not the agent's claim—is what the user
reviews.

The user can inspect the artifact, request revisions in ordinary language,
compare versions, and repeat the loop until the result meets the intended need.
Every revision remains tied to the request, the exact candidate work, and new
verification evidence. The agent preserves this context so the user does not
have to restart the explanation at each turn.

“Talk to my agent first” does not remove the project owner or maintainer. It
creates a governed first layer between routine client requests and the person
responsible for the project. The specialized agent handles clarification,
bounded execution, artifact preparation, and revision tracking. It escalates
when a request is outside scope, evidence is insufficient, a policy decision is
needed, or the user has accepted an artifact for maintainer consideration.

This gives the client a faster, more direct way to shape the result while
protecting the maintainer from premature interruptions and unstructured change
requests. When human review is needed, the maintainer receives a prepared
artifact, the revision history, verification evidence, and the decisions that
remain—not merely a chat transcript.

### Intended client experience

1. Describe the desired outcome to the project's agent.
2. Answer any project-specific clarification questions.
3. Receive a verified artifact that can be opened or inspected directly.
4. Accept it, reject it, or request a revision.
5. Review each new version with a clear explanation of what changed.
6. Send the accepted version to the maintainer for a separate technical or
   governance decision.

### Intended maintainer experience

- Define the agent's project knowledge, scope, permissions, verification, and
  artifact contract once.
- Receive only requests that require authority, judgment, or final approval.
- Review a bounded candidate with its artifact, evidence, and feedback history.
- Retain control over integration, release, sensitive data access, and changes
  to the agent's capabilities.

The reusable product unit is a **Specialized Agent Bundle**:

| Component | Responsibility |
|---|---|
| Project skill | Domain knowledge, terminology, workflow, and communication |
| Scope policy | Enforced permissions, boundaries, limits, and stop conditions |
| Isolated workspace | Contains candidate work without exposing unrelated resources |
| Independent verifier | Determines whether the candidate satisfies defined checks |
| Artifact builder | Produces the exact output the user needs to review |
| Review workflow | Records feedback, revisions, comparison, and user acceptance |
| Maintainer gate | Separately decides whether accepted work may enter the project |

The shared framework provides these controls, but every deployed bundle remains
bound to one approved project, one purpose, one policy, and one artifact
workflow.

The primary lifecycle is:

> request -> bounded work -> independent verification -> review artifact ->
> user feedback -> revised artifact -> user acceptance -> maintainer decision

Producing source changes is an internal step. The user-facing result is a safe,
versioned artifact that can be understood and reviewed without development
tools.

## Design principles

- [ ] Specialization is enforced, not merely described in a prompt.
- [ ] The agent receives only the capabilities and context required for its
      declared purpose.
- [ ] Configuration, policy, credentials, and authorization remain outside
      user and agent control.
- [ ] Every material result is independently verified before review.
- [ ] Every reviewable artifact is immutable, attributable, and reproducible.
- [ ] Review and revision are first-class workflow states, not chat conventions.
- [ ] User acceptance and technical approval are separate decisions.
- [ ] Failure or uncertainty stops progress safely and preserves evidence.
- [ ] New capabilities require an explicit policy change and human approval.

## Non-goals

- [ ] Do not let users or agents select arbitrary projects, tools, models, data
      sources, credentials, policies, or release destinations.
- [ ] Do not provide a mode that turns a specialized agent into a general or
      privileged agent.
- [ ] Do not permit one agent to access another project's context, work,
      artifacts, users, or data.
- [ ] Do not add broad capabilities only for convenience or future flexibility.
- [ ] Do not allow the agent to approve its own work.
- [ ] Do not automatically integrate, release, or deploy a user-accepted result.
- [ ] Do not treat model completion or self-reported checks as proof of success.
- [ ] Do not replace a previously reviewed artifact when creating a revision.

## P0 — define the specialized-agent contract

Each agent must have a versioned, system-controlled contract.

- [ ] Define a stable agent identity, project, purpose, owner, intended users,
      and authorized reviewers.
- [ ] Define the requests the agent may handle and explicit out-of-scope cases.
- [ ] Define the information the agent may read, change, generate, and disclose.
- [ ] Define protected resources that remain inaccessible under all conditions.
- [ ] Define allowed operations, validation checks, artifact types, resource
      limits, retention rules, and escalation conditions.
- [ ] Define the user decisions supported by the review workflow.
- [ ] Define the maintainer or governance role responsible for final approval.
- [x] Validate the contract before execution startup and fail closed if it is
      missing, invalid, unapproved, or requests unsupported capabilities.
- [ ] Record the contract version with every request, result, artifact, review,
      and approval decision.
- [ ] Never allow conversation content, project content, tool output, or an
      agent-authored file to widen the contract.

## P0 — require a developer-authored governance template

Every Specialized Agent Bundle must include a completed and approved
`specialized-agent-governance.yaml`, based on
[`templates/specialized-agent-governance.yaml`](templates/specialized-agent-governance.yaml).
The template is part of the agent's enforceable contract, not supporting
documentation.

- [ ] Require the developer to state, in plain language, what the agent may do,
      must do, must not do, and must escalate.
- [ ] Require matching structured controls for every declared capability. Plain
      language may narrow permissions but can never grant a permission absent
      from the structured policy.
- [ ] Reject ambiguous, conflicting, incomplete, or overly broad declarations.
- [ ] Default every capability to denied and every resource budget to zero until
      the developer supplies an approved value.
- [x] Validate the template against a versioned schema before the agent can be
      enabled through the deployment adapter.
- [ ] Require named developer, project owner, governance owner, approver,
      approval time, contract version, and review or expiry date.
- [ ] Record the exact governance-template version and integrity hash with every
      request, agent run, verification, artifact, review, and escalation.
- [ ] Require reapproval when capabilities, data access, tools, models, budgets,
      artifact types, verification, or review authority change.
- [ ] Refuse runtime overrides from users, agents, project content, tools, or
      environment-provided free text.

### Setup-agent interview

Use a dedicated setup agent to create the first draft of the governance file.
Follow
[`templates/setup-agent-questionnaire.md`](templates/setup-agent-questionnaire.md)
so every specialized bundle is configured through the same explicit decisions.

- [ ] Keep the setup agent separate from the client-facing working agent. The
      setup agent configures policy; it does not perform project work.
- [ ] Ask focused questions one at a time and write each confirmed answer to the
      corresponding structured field.
- [ ] Ask about project purpose, intended users, supported and prohibited
      requests, allowed resources, data classification, operations, tools,
      external communication, models, artifacts, verification, review roles,
      escalation, retention, and incident ownership.
- [ ] Ask the developer to set hard token, cost, time, tool-call, concurrency,
      revision, storage, file-change, and artifact-size limits.
- [ ] Provide a safe recommended choice where useful, but clearly distinguish a
      recommendation from a confirmed answer.
- [x] Never infer or silently broaden permissions from the project description.
      Unanswered or uncertain permissions remain denied.
- [x] Allow the setup agent to write only a `draft`, disabled configuration. It
      cannot approve the policy, mark effective permissions verified, or enable
      the working agent.
- [x] Validate the draft continuously and identify missing required answers.
      Conflict and unenforceable-control detection remain adapter work.
- [x] End with an effective-policy summary in plain language: what the agent can
      access, change, call, produce, disclose, approve, and never do.
- [x] Report structured scope/model/network conflicts and controls that require
      external enforcement before approval.
- [ ] Show the complete configuration diff and risk summary before asking the
      developer and governance owner for approval.
- [ ] Preserve the setup session ID, questionnaire version, confirmed answers,
      unresolved questions, and approval record for audit.

### CLI-agent enforcement adapters

CLI agents do not share one permission model and do not automatically follow
the governance file. Treat the file as framework policy that must be enforced
outside the model through a versioned adapter and independent controls.

- [ ] Maintain a capability matrix for every supported CLI agent and version,
      covering filesystem access, network access, tools, commands, extensions,
      external services, model selection, configuration precedence, approvals,
      token reporting, cancellation, and session continuation.
- [ ] Map each governance field to a native CLI control, an external enforcement
      control, or `unsupported`. Never map an enforceable requirement to prompt
      text alone.
- [ ] Refuse to start when a required policy rule cannot be enforced for the
      selected CLI agent and version.
- [ ] Verify the actual CLI version and effective capabilities at startup and
      record the adapter and policy-mapping versions with each job.
- [ ] Prevent personal, repository, environment, extension, or session-level CLI
      configuration from widening the approved policy.
- [ ] Enforce token, cost, time, and tool budgets externally when the CLI does
      not provide reliable native limits.
- [ ] Terminate safely at a hard limit, preserve candidate work and evidence,
      and report `budget_exhausted`; do not depend on the CLI agent to stop
      itself.
- [ ] Add contract tests for each adapter proving both allowed behavior and
      denied behavior against the real supported CLI version.
- [ ] Re-run adapter certification whenever the CLI agent or runtime version
      changes.

### Token, cost, and workload governance

- [ ] Require hard limits for input tokens per model call, output tokens per
      call, turns per job, cumulative tokens per job, cumulative tokens per
      revision, and cumulative tokens per review cycle.
- [ ] Require limits for cost per job and review cycle, wall-clock duration,
      tool calls, retries, concurrent jobs, queued jobs, revisions, and artifact
      size.
- [ ] Define which context sources may consume the token budget and the maximum
      allocation for conversation history, project context, retrieved context,
      tool results, and system instructions.
- [ ] Define the history policy explicitly: retain, summarize, retrieve, or
      discard. Never silently remove information required to understand the
      current artifact or revision request.
- [ ] Perform a preflight budget check before every model or tool call and stop
      safely when the remaining budget cannot support the next required step.
- [ ] Measure actual usage after every call and stop at the first exceeded hard
      limit. Do not silently increase a budget, switch to an unapproved model,
      or continue in an unverified reduced-quality mode.
- [ ] Distinguish `budget_exhausted` from technical failure and policy refusal.
      Preserve work and tell the reviewer or maintainer what remains.
- [ ] Include token, cost, duration, tool-call, and revision usage in governance
      evidence, with alerts that do not expose sensitive prompt content.
- [ ] Allow a budget increase only through an authenticated, recorded approval
      that creates a new policy version; a chat message cannot increase it.

### Capability governance

- [ ] Require explicit allowlists for readable resources, writable resources,
      operations, tools, data sources, external destinations, and artifact
      types.
- [ ] Require explicit denials for privileged operations, unrelated resources,
      credential access, policy modification, self-approval, cross-project
      access, and release actions.
- [ ] Prefer named, brokered actions over arbitrary commands or unrestricted
      tools.
- [ ] Require an escalation owner and reason for every capability the agent
      cannot exercise directly.
- [ ] Check effective permissions at startup and before each protected action;
      do not assume the deployed environment matches the declared policy.
- [ ] Fail closed when the actual capability set is broader than the approved
      template.

## P0 — implement a governed lifecycle

- [ ] Use an explicit state model:

      `submitted -> scoped -> working -> verification -> artifact_building ->`
      `review_ready -> revision_requested | accepted | rejected`

- [ ] Allow any processing state to transition to `needs_attention` when safe
      continuation is uncertain.
- [ ] Require the request to fit the agent contract before work begins.
- [ ] Preserve the relationship between the request, candidate result,
      verification evidence, artifact, and review decision.
- [ ] Prevent editing while an artifact is awaiting review unless the reviewer
      explicitly requests a revision.
- [ ] Resume revisions from the exact result associated with the reviewed
      artifact, not from an ambiguous or later state.
- [ ] Re-run verification and artifact creation after every revision.
- [ ] Keep all prior review states and artifact versions available according to
      the retention policy.
- [ ] Require a separate maintainer decision after user acceptance.

## P0 — make verification independent

- [ ] Treat agent output as a candidate requiring verification.
- [ ] Verify the exact candidate version in a clean, controlled environment.
- [ ] Use predefined checks that neither the user nor agent can alter.
- [ ] Include functional, policy, security, privacy, and artifact-specific
      checks appropriate to the project.
- [ ] Store structured verification evidence, including the candidate version,
      policy version, verifier version, checks performed, outcomes, timestamps,
      and relevant diagnostic output.
- [ ] Produce a review artifact only when every mandatory check passes.
- [ ] Use `review_ready` rather than “done” while human review is outstanding.
- [ ] Treat missing, incomplete, contradictory, or stale evidence as a failed
      verification state.
- [ ] Ensure the verifier cannot modify the candidate it evaluates.

## P0 — make the review artifact the primary output

### Artifact requirements

- [ ] Define a versioned `ReviewArtifact` with:
      - a stable artifact and revision identifier;
      - the specialized agent and project identity;
      - the originating request and candidate version;
      - a parent artifact identifier for revisions;
      - an immutable content hash;
      - a human-readable preview or downloadable result;
      - a concise description of what changed;
      - verification status and supporting evidence;
      - known limitations, assumptions, and unresolved decisions;
      - creator, timestamps, classification, and retention metadata.
- [ ] Make every artifact immutable. A revision creates a new linked version.
- [ ] Ensure the artifact can be reviewed without access to development tools.
- [ ] Present domain-relevant output first; keep technical evidence available
      separately for maintainers.
- [ ] Bind the review location to one exact artifact version.
- [ ] Prevent incomplete, stale, substituted, or unverified content from being
      presented as the current artifact.
- [ ] Enforce allowed artifact types, formats, sizes, and content policies.
- [ ] Validate that artifact inputs and outputs remain within approved
      boundaries throughout collection, transformation, storage, and delivery.

### Supported review actions

- [ ] Provide three explicit user actions:
      - **Accept for maintainer review**;
      - **Request revision**;
      - **Reject or abandon**.
- [ ] Attach every review action and comment to the exact artifact version the
      user saw.
- [ ] On a revision request, summarize the requested differences before work
      resumes.
- [ ] Create a new candidate, new verification record, and new artifact for
      every revision.
- [ ] Let the user compare the new artifact with the immediately preceding
      version.
- [ ] Allow a user to leave and later resume the pending review without
      restarting completed work.
- [ ] Preserve rejected and superseded artifacts for audit according to policy.

### Maintainer handoff

- [ ] User acceptance changes the state to `accepted`; it does not authorize
      integration, release, or deployment.
- [ ] Generate a maintainer handoff containing the accepted artifact, revision
      history, candidate changes, verification evidence, unresolved risks, and
      the user's acceptance identity and timestamp.
- [ ] Require the maintainer to approve, request revision, or reject.
- [ ] Keep release and production actions outside the client-agent workflow.
- [ ] If downstream release automation is introduced, require a separate,
      authenticated maintainer action against the accepted candidate version.

## P0 — security requirements

### Least privilege and isolation

- [ ] Run each specialized agent in an isolated workspace with access only to
      its approved project resources.
- [ ] Separate agent execution, verification, artifact construction, artifact
      delivery, and final approval into distinct trust roles.
- [ ] Give each role only the data and operations required for its stage.
- [ ] Deny access to unrelated projects, administrative interfaces, production
      systems, and credential stores.
- [ ] Disable outbound communication by default. Permit only explicit,
      project-specific destinations when unavoidable.
- [ ] Prevent agent-controlled content or metadata from causing trusted
      services to execute unintended commands, load unintended resources, or
      disclose protected information.
- [ ] Treat all agent-produced paths, files, links, configuration, metadata,
      and executable content as untrusted.
- [ ] Use immutable versions and integrity checks when passing work between
      trust roles.

### Data protection

- [ ] Classify project data before granting an agent or artifact process access.
- [ ] Minimize data exposure and prefer approved summaries, aggregates, or
      synthetic inputs.
- [ ] Do not execute agent-authored logic with sensitive data unless a separate,
      explicitly approved control can prove the output cannot disclose it.
- [ ] Check artifacts for secrets, personal identifiers, unexpected row-level
      content, active content, hidden data, and unapproved external references.
- [ ] Record the data sources, transformations, policy checks, and approvals
      used to produce every sensitive artifact.
- [ ] Prevent logs, errors, prompts, histories, and evidence from becoming an
      uncontrolled copy of protected data.

### Identity and authorization

- [ ] Authenticate services separately from end users.
- [ ] Derive user identity from verified authentication rather than accepting
      an untrusted identity assertion.
- [ ] Bind requests, workspaces, results, artifacts, feedback, and decisions to
      the authorized user and specialized-agent instance.
- [ ] Enforce authorization on reads, writes, reviews, revisions, and approvals.
- [ ] Ensure knowledge of an identifier does not grant access to its resource.
- [ ] Use short-lived, scoped credentials and support independent revocation for
      each specialized agent.
- [ ] Record authorization and policy decisions without exposing credentials.

### Resource and abuse controls

- [ ] Limit request size, history size, concurrent and queued work, execution
      time, compute, memory, storage, files changed, artifact size, revisions,
      and workspace lifetime.
- [ ] Rate-limit authentication failures, submissions, status reads, artifact
      access, and revision requests.
- [ ] Reject direct and nested attempts to override protected configuration.
- [ ] Pin trusted runtime components and test upgrades before rollout.
- [ ] Provide a project-level emergency stop that preserves evidence and does
      not affect unrelated specialized agents.

## P1 — governance

### Roles

- [ ] **Requester/reviewer:** defines the desired outcome, reviews artifacts,
      requests revisions, and may accept an artifact for maintainer review.
- [ ] **Specialized agent:** performs bounded work but cannot change its scope,
      verification rules, or permissions.
- [ ] **Verifier:** evaluates the candidate independently and cannot approve or
      modify its own checks.
- [ ] **Artifact service:** constructs and delivers only approved artifact types
      from verified candidates.
- [ ] **Maintainer:** decides whether an accepted result may enter the project.
- [ ] **Governance owner:** approves contracts, capabilities, data access,
      retention, exceptions, and incident actions.

### Policy management

- [ ] Deny capabilities unless they are explicitly granted.
- [ ] Allow project instructions to narrow behavior but never widen enforced
      capabilities.
- [ ] Require governance approval for new data access, operations, artifact
      types, integrations, external services, or release authority.
- [ ] Resolve policy conflicts toward the narrower permission and a safe stop.
- [ ] Version and review all policy changes before activation.
- [ ] Record every exception or manual override with the actor, reason,
      timestamp, affected object, duration, and before/after policy state.
- [ ] Periodically revalidate that each capability is still necessary for the
      agent's specialized purpose.

### Audit and retention

- [ ] Maintain append-only events for authentication, authorization, scope
      decisions, work, verification, artifacts, feedback, acceptance,
      maintainer decisions, policy changes, and manual recovery.
- [ ] Link evidence to immutable identities and integrity hashes.
- [ ] Make every maintainer handoff reproducible from the recorded candidate,
      policies, verifier, and artifact versions.
- [ ] Define retention and deletion rules for conversations, workspaces,
      artifacts, verification evidence, and rejected work.
- [ ] Never automatically destroy unresolved or uncertain work.
- [ ] Test backup, recovery, evidence export, and credential revocation.

## P1 — failure and uncertainty handling

- [ ] Enter `needs_attention` after an abnormal exit, timeout, integrity
      failure, uncertain candidate state, verifier failure, artifact failure,
      authorization anomaly, or policy conflict.
- [ ] Preserve the candidate and evidence without automatically retrying,
      resetting, deleting, or continuing.
- [ ] Require an authorized maintainer to inspect and explicitly resume,
      abandon, or restart uncertain work.
- [ ] Clearly distinguish rejected work, technical failure, policy refusal,
      user abandonment, and maintainer intervention.
- [ ] Never display a previous artifact as the result of a failed revision.
- [ ] State uncertainty and missing evidence directly rather than inferring
      success.

## P2 — review experience

- [ ] Introduce each agent with its fixed purpose, available artifact, and main
      limitations.
- [ ] Ask only questions required to produce the specialized artifact.
- [ ] Show understandable stages: scoping, working, verifying, preparing the
      artifact, and ready for review.
- [ ] Keep raw technical activity out of the primary user view while retaining
      it as evidence.
- [ ] Lead every `review_ready` response with the artifact and review actions.
- [ ] For revisions, show what changed since the reviewed version, the new
      verification result, and any remaining limitations.
- [ ] Make expired sessions, dropped connections, long-running work, failures,
      and return visits recoverable without hiding the actual state.
- [ ] Validate the review workflow across the devices and accessibility modes
      used by intended reviewers.

## P2 — framework validation

- [ ] Maintain portable unit tests, isolated integration tests, adversarial
      security tests, artifact reference tests, and deployment attestations.
- [ ] Test the complete request -> verification -> artifact -> revision -> new
      artifact -> acceptance -> maintainer-decision lifecycle.
- [ ] Prove that every prior artifact remains unchanged after later revisions.
- [ ] Prove that an unverified candidate cannot produce a review artifact.
- [ ] Prove that user acceptance cannot trigger integration or deployment.
- [ ] Prove that users and agents cannot cross project or identity boundaries.
- [ ] Validate the framework with multiple materially different specialized
      projects without adding user-controlled generality.
- [ ] Measure artifact completion, revision frequency, user review time,
      maintainer review time, verification failures, policy refusals, unsafe
      attempts, and abandonment reasons.
- [ ] Evaluate the framework by review quality and reduced maintainer burden,
      not by the number of agent capabilities.

## Definition of done for the next milestone

- [ ] Each deployment is bound to one specialized project and one approved
      contract.
- [ ] Neither users nor agents can switch projects or widen capabilities.
- [ ] Candidate work is independently verified against immutable versions.
- [ ] Only verified candidates can produce immutable review artifacts.
- [ ] A user can review artifact v1, request a revision, compare and review v2,
      and accept v2 for maintainer consideration.
- [ ] User acceptance cannot integrate, release, or deploy the result.
- [ ] Every state transition, artifact version, review action, exception, and
      maintainer decision is attributable and auditable.
- [ ] Sensitive data cannot be disclosed through agent work, verification,
      artifact construction, or delivery.
- [ ] Uncertain work stops safely, remains inspectable, and is never presented
      as success.
