# Specialized Client Agents — TODO

Last updated: 2026-09-02

## Product decision

Build **specialized, project-specific client agents**, not a general coding
agent and not a runtime that lets a client choose arbitrary repositories,
tools, models, data, or deployment targets.

The shared code is infrastructure only. Every deployed agent must be bound to
one known project, one defined client role, one policy, and one review-artifact
workflow. A project skill supplies domain behavior; the gateway enforces the
boundaries that instructions cannot.

The primary product workflow is:

> request -> bounded change -> independent verification -> review artifact ->
> client feedback -> revised artifact -> client acceptance -> maintainer review

Producing code is an implementation detail. The client-facing result is a safe,
versioned artifact that the user can inspect and revise without needing Git or
a terminal.

## Non-goals — keep the agents specialized

- [ ] Do not add a client-supplied repository, project, branch, model, system
      prompt, sandbox, command, credential, data source, or deployment target.
- [ ] Do not add a project picker or a “developer mode” to the client UI.
- [ ] Do not let one agent read another project's repository, memory, jobs,
      workspaces, artifacts, data, or credentials.
- [ ] Do not provide arbitrary shell, MCP, internet, package-installation, or
      deployment access merely to make the bundle more flexible.
- [ ] Do not auto-push, auto-open or merge PRs, deploy production, or interpret
      client acceptance as maintainer approval.
- [ ] Do not turn the shared runtime into a public multi-purpose agent API.
- [ ] Do not claim success from the model's exit code or self-reported tests.
- [ ] Do not overwrite a prior review artifact when producing a revision.
- [ ] Do not render agent-authored code against sensitive data as a convenience
      feature.

## P0 — close security boundary crossings before another client pilot

### Prevent Git metadata from becoming an escape path

- [ ] Make `.git` metadata unavailable or read-only to the agent process,
      including the default Copilot provider.
- [ ] Run every gateway-owned Git command with a server-owned empty
      `core.hooksPath`; never execute hooks from an agent-writable repository.
- [ ] Ignore repository-local Git configuration when the gateway stages,
      diffs, or commits. Review Git aliases, filters, attributes, submodules,
      worktree config, and external diff/text-conversion drivers as equivalent
      execution surfaces.
- [ ] Add adversarial tests that attempt to plant `pre-commit`, `post-commit`,
      clean/smudge filters, external diff commands, aliases, and malicious
      repository config, and prove that no gateway-side command executes them.
- [ ] Treat any unexpected `.git` mutation as `needs_attention`; preserve the
      workspace and do not commit, verify, or publish it.

### Make artifact collection path-safe

- [ ] Reject symlinks, hard links where detectable, devices, sockets, FIFOs,
      and non-regular files at every allowed artifact source path.
- [ ] Open artifact inputs without following symlinks and verify their resolved
      location remains inside the expected verified workspace.
- [ ] Copy through already-open file descriptors into a new staging directory;
      do not perform a second path lookup after validation.
- [ ] Enforce output count, type, and size limits before publishing.
- [ ] Publish a complete artifact version atomically. A failed copy must leave
      the prior version intact rather than expose a partially updated preview.
- [ ] Add regression tests reproducing attempted path traversal, symlink swaps,
      nested symlinks, oversized outputs, and partial publication failures.

### Separate untrusted source from sensitive data

- [ ] Stop rendering arbitrary agent-modified R, Quarto, Python, JavaScript, or
      shell code with real client data, even inside a networkless sandbox. The
      code can disclose the data through the generated artifact itself.
- [ ] Define a project-specific artifact contract. Prefer an approved renderer
      consuming a typed, aggregate-only intermediate representation over
      executing agent-authored code with data access.
- [ ] Keep raw data outside agent workspaces and artifact builders. Expose only
      explicitly approved de-identified summaries or aggregate query results.
- [ ] Require human approval for any workflow that uses real or sensitive data,
      and record the approver, source version, query/specification, output hash,
      and disclosure checks.
- [ ] Scan candidate artifacts for secrets, direct identifiers, unexpected
      row-level output, embedded source data, external links, active content,
      and unapproved network references before they become reviewable.

### Reduce the privilege of trusted services

- [ ] Revisit the existing decision to run the gateway as `yeli`. Before any
      external or multi-user use, run each project agent and its artifact
      builder under a dedicated unprivileged identity or isolated workload with
      no sudo, Docker socket, unrelated home directory, or production access.
- [ ] If `yeli` remains for an internal pilot, record it as a temporary
      product-launch blocker rather than a permanent product assumption.
- [ ] Separate the agent runner, verifier, artifact builder, and publisher so a
      compromise of one does not inherit every capability.
- [ ] Give the publisher write access only to a versioned non-production review
      store. It must not receive agent credentials, repository credentials,
      source data, or production deployment rights.
- [ ] Replace Open WebUI host networking before treating this as a reusable
      deployment. Use a narrow authenticated IPC path so the UI cannot reach
      unrelated loopback services.

### Make authorization project- and user-aware

- [ ] Replace the service-wide bearer secret as the user authorization model.
      Authenticate the UI-to-gateway service separately and pass a signed,
      verified user identity.
- [ ] Derive `user_id` from the authenticated identity; never trust a caller-
      supplied identity field by itself.
- [ ] Bind every chat, job, event stream, workspace, feedback item, and artifact
      to both the project-agent instance and the authorized user or review
      group.
- [ ] Enforce ownership checks on reads as well as writes. Knowing a job or
      artifact ID must not grant access.
- [ ] Use opaque, unguessable identifiers and record authorization decisions.
- [ ] Add negative tests for cross-user, cross-chat, and cross-project reads,
      revision requests, artifact access, and approval attempts.

## P0 — make verification a gateway-owned decision

- [ ] Replace the current success interpretation with an explicit state model:

      `queued -> editing -> agent_finished -> verification_running ->`
      `artifact_building -> review_ready`

      Any stage may transition to `rejected` or `needs_attention`.
- [ ] Run project checks independently after the agent exits. The model may
      suggest or run tests, but its report is not verification evidence.
- [ ] Run verification against the exact candidate commit in a clean,
      project-specific environment with no agent session state.
- [ ] Define allowlisted verification commands in the server-owned project
      policy. The client and agent cannot change them.
- [ ] Store structured evidence: candidate commit, command, environment/image
      version, start/end times, exit status, relevant output, produced files,
      policy results, and verifier version.
- [ ] Require all mandatory checks to pass before artifact building. Use the
      literal state `review_ready`, not “done” or “successful,” while human
      review remains outstanding.
- [ ] Freeze the chat as `needs_attention` after every timeout, abnormal exit,
      gateway exception, dirty tree, metadata mutation, commit uncertainty,
      verifier crash, or publication failure.
- [ ] Never silently reset, delete, retry, or continue from an uncertain
      workspace. A maintainer must inspect and explicitly resume or abandon it.

## P0 — make the review artifact the core output

### Artifact package

- [ ] Define a versioned `ReviewArtifact` record with at least:
      - artifact and revision IDs;
      - project-agent ID and chat/job IDs;
      - parent artifact ID for revisions;
      - candidate commit and base commit;
      - immutable content hash;
      - human-readable preview or downloadable file;
      - concise change summary and affected areas;
      - verification status and evidence link;
      - known limitations and maintainer actions;
      - creator, timestamps, data classification, and retention policy.
- [ ] Keep every artifact immutable. A revision creates version 2 linked to
      version 1; it never replaces version 1 in place.
- [ ] Make the artifact usable without Git knowledge. The user should see the
      changed report/dashboard/document, what changed, what was checked, and
      what still requires judgment.
- [ ] Include a technical diff and command log for the maintainer, but do not
      force the client reviewer to understand them.
- [ ] Ensure the preview URL resolves to the exact immutable artifact version,
      not a mutable directory that later revisions overwrite.
- [ ] Record artifact access and review actions without recording secrets or
      unnecessary sensitive content.

### Review actions

- [ ] Provide exactly three client review actions:
      - **Accept for maintainer review** — the artifact meets the client's need;
      - **Request revision** — provide feedback tied to this artifact version;
      - **Reject / abandon** — preserve the record but stop work.
- [ ] Treat free-text feedback as a new bounded job in the same chat and
      workspace, based on the exact reviewed commit and artifact version.
- [ ] Show the agent the review feedback and relevant prior artifact summary,
      not unrelated chats or another project's history.
- [ ] Re-run the full independent verification and artifact build after every
      revision. Prior verification does not carry forward.
- [ ] Present a clear version history so the client can compare the current
      artifact with the immediately preceding one.
- [ ] Let the client return from a dropped session and resume the pending review
      without restarting the coding job.
- [ ] Do not allow new editing while an artifact is being reviewed unless the
      reviewer explicitly requests a revision; this prevents competing states.

### Acceptance and maintainer handoff

- [ ] Client acceptance moves the item to `accepted_by_client`; it does not
      merge code, push a branch, or deploy anything.
- [ ] Generate a maintainer handoff containing the accepted artifact, complete
      revision lineage, final diff, verification evidence, commits, unresolved
      risks, and the client's acceptance identity and timestamp.
- [ ] Require a separate maintainer decision: approve for integration, request
      another revision, or reject.
- [ ] Keep production deployment outside the client-agent service. If added
      later, it must be a separate maintainer-authorized workflow consuming an
      accepted and reviewed commit.

## P1 — define one specialized-agent bundle contract

- [ ] Extract one shared runtime from the duplicated compliance and gambling
      gateways. Security fixes must land once and apply to every project agent.
- [ ] Keep deployment instances single-project. A running gateway loads exactly
      one server-installed manifest and refuses runtime project switching.
- [ ] Define a versioned, validated project manifest containing:
      - stable project-agent ID, name, purpose, owner, and intended reviewers;
      - fixed repository source and base branch;
      - branch naming and workspace retention;
      - readable, editable, generated, protected, and forbidden paths;
      - allowed tools and commands;
      - independent verification checks;
      - artifact builder and allowed outputs;
      - data classification and permitted summaries;
      - review roles, stop rules, and escalation owner;
      - resource limits and retention periods.
- [ ] Fail startup if the manifest is missing, invalid, unsigned/unapproved, or
      asks for capabilities the runtime policy does not support.
- [ ] Keep the project skill separate from the enforcement manifest. The skill
      explains domain behavior; it cannot grant capabilities.
- [ ] Version the runtime, manifest, skill, verifier, and artifact builder in
      every job and artifact record.
- [ ] Put project-specific preview logic behind a narrow plugin contract rather
      than conditionals or copied gateway code.

### Proposed repository layout

```text
runtime/
  gateway/
  runners/
  verifier/
  artifact_store/
  security_tests/
projects/
  compliance/
    agent.yaml
    policy.yaml
    SKILL.md
    verifier.yaml
    artifact_builder/
  gambling/
    agent.yaml
    policy.yaml
    SKILL.md
    verifier.yaml
    artifact_builder/
deploy/
  openwebui/
  systemd-or-container/
```

## P1 — governance model

### Roles and separation of duties

- [ ] **Client requester/reviewer:** requests changes, reviews artifacts, asks
      for revisions, and accepts the result for maintainer review.
- [ ] **Specialized agent:** edits only the fixed project workspace within its
      policy. It cannot approve itself or expand its own scope.
- [ ] **Verifier:** independently evaluates the exact candidate commit and
      cannot modify it.
- [ ] **Artifact builder:** creates only the allowlisted review output from a
      verified commit under the project's data policy.
- [ ] **Maintainer:** reviews code and evidence and alone decides whether the
      change may enter the project.
- [ ] **Operator/security owner:** installs manifests, manages identities and
      secrets, reviews incidents, and changes runtime policy.

### Policy rules

- [ ] Capabilities are denied unless explicitly granted in the installed
      project policy.
- [ ] Prompt text, skills, repository files, tool output, and review feedback
      are all untrusted inputs and cannot modify policy.
- [ ] A skill or repository instruction may further restrict behavior but may
      never widen the runtime's capabilities.
- [ ] Scope expansion, new data access, new commands, new artifact types, new
      external services, and production access require an operator-reviewed
      policy version—not a chat instruction.
- [ ] Conflicts resolve toward the narrower permission and `needs_attention`.
- [ ] Every manual override requires an actor, reason, timestamp, affected
      object, and before/after policy state.

### Audit and retention

- [ ] Use append-only audit events for authentication, authorization, job state,
      commands, commits, verification, artifacts, feedback, acceptance,
      maintainer decisions, policy changes, and manual recovery.
- [ ] Link all evidence to immutable IDs and hashes so a handoff can be replayed
      against the same source and artifact versions.
- [ ] Redact secrets and minimize personal or client data in prompts, logs,
      command output, exceptions, and artifacts.
- [ ] Define project-specific retention for chats, workspaces, artifacts, audit
      evidence, and rejected/abandoned changes.
- [ ] Provide explicit archival and deletion procedures. Automatic cleanup must
      never destroy an unresolved or `needs_attention` workspace.
- [ ] Back up the audit database and artifact metadata separately from agent
      credentials and sensitive project data; test restoration.

## P1 — limits and abuse controls

- [ ] Enforce limits on message and history bytes, queued jobs per user, total
      runtime, subprocesses, CPU, memory, disk, files changed, artifact size,
      revisions per request, and workspace lifetime.
- [ ] Reject nested or encoded attempts to supply server-owned configuration.
- [ ] Disable outbound network by default for agent tools, verifier, and
      artifact builder. Allow only project-specific destinations through an
      explicit broker when unavoidable.
- [ ] Pin runner, UI, system dependencies, verifier images, and artifact tools
      by immutable version or digest and test upgrades before rollout.
- [ ] Rate-limit authentication failures, job creation, event reads, artifact
      reads, and revision requests.
- [ ] Define incident actions: disable one project agent, revoke its service
      credential, preserve evidence, invalidate review URLs, and block pending
      handoffs without affecting other projects.

## P2 — user experience for the review-revise loop

- [ ] Start every specialized agent with its fixed identity, purpose, current
      review target, and a short statement of what it cannot do.
- [ ] Ask only questions necessary to produce the project's artifact; do not
      expose infrastructure choices to the client.
- [ ] Stream understandable stages: queued, editing, verifying, building
      artifact, ready for review. Keep raw tool chatter in maintainer evidence.
- [ ] On `review_ready`, lead with the artifact and review actions. Put code
      details after the client-facing result.
- [ ] When revision is requested, acknowledge the exact artifact version and
      summarize the requested differences before editing.
- [ ] For every new artifact version, show what changed since the version the
      user reviewed, verification results, and any remaining limitation.
- [ ] Make rejected, failed, and `needs_attention` states honest and actionable;
      never display a stale preview as the new result.
- [ ] Test the workflow with keyboard-only navigation, mobile review, dropped
      connections, expired sessions, long-running jobs, and return visits.

## P2 — validation and product-discovery gates

- [ ] Split tests into portable unit tests, isolated integration tests,
      adversarial security tests, artifact golden tests, and host/deployment
      attestation. Run portable and adversarial suites in CI.
- [ ] Add end-to-end tests for request -> verified artifact -> revision -> new
      verified artifact -> client acceptance -> maintainer handoff.
- [ ] Add tests proving that artifact version 1 remains available and unchanged
      after version 2 is produced.
- [ ] Add tests proving that no unverified commit can produce a review artifact
      and no client acceptance can trigger integration or deployment.
- [ ] Convert compliance and gambling to the shared runtime without weakening
      either project's policy.
- [ ] Onboard a third, materially different project using only a manifest,
      skill, verifier, and artifact builder. Target less than one working day
      and no runtime source edits.
- [ ] Pilot 20–30 real requests with 3–5 authorized client reviewers. Measure:
      - percentage reaching a verified review artifact;
      - artifact revisions per accepted request;
      - client time to review;
      - maintainer review time;
      - verification and policy failures;
      - unsafe or out-of-scope attempts;
      - abandoned requests and reasons.
- [ ] Decide whether to continue product development from demonstrated review
      value and reduced maintainer burden—not from the number of agent features.

## Definition of done for the next milestone

- [ ] One shared runtime serves compliance and gambling as two separate,
      single-project deployments.
- [ ] Each deployment loads a fixed manifest and project skill; neither the
      client nor agent can switch projects or widen capabilities.
- [ ] The Git-hook and artifact-symlink reproductions are blocked by passing
      adversarial tests.
- [ ] Untrusted source is never executed with sensitive data access.
- [ ] The gateway independently verifies the exact candidate commit.
- [ ] Only verified commits produce immutable review artifacts.
- [ ] A client can review artifact v1, request a revision, compare and review
      v2, and accept v2 for maintainer review.
- [ ] Client acceptance cannot push, merge, or deploy.
- [ ] Every state transition, artifact version, review action, and maintainer
      handoff is attributable and auditable.
- [ ] No unresolved failure is presented as success or automatically erased.
