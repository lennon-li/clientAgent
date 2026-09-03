# Deployment lessons: route governance and machine truth

Date: 2026-09-03

This note captures reusable lessons from a project-specific deployment. It
intentionally omits the deployment's identity, private paths, local aliases,
provider choices, and client data.

## Lessons learned

1. **Routing is policy, not convenience plumbing.** Choosing an implementer,
   escalation path, or verifier affects capability, permission, independence,
   cost, and evidence quality. The policy needs its own versioned, normalized,
   content-addressed contract.
2. **Policy evaluation must not select or execute a route.** Normalization and
   digesting are pure operations. Route selection remains a separate
   server-owned responsibility, and a coherent proposal remains pending human
   approval.
3. **A proposal is an untrusted claim.** Repeating a profile's provider, model,
   capabilities, or permissions in a proposal does not make those facts true.
   The validator must match the proposal to the exact approved profile digest
   and reject any mismatch.
4. **Availability is weaker than eligibility.** An executable or model can be
   present while still being unapproved, untrusted, over quota, insufficiently
   isolated, or incapable of the task. Discovery therefore produces only
   short-lived machine-truth evidence and never flips an eligibility bit.
5. **Independence is multidimensional.** A different profile identifier alone
   may still reuse the implementer's provider or model family. Higher-risk
   work needs explicit, testable independence rules for profile, provider
   family, model family, and required validation capability.
6. **Fallbacks are policy changes unless declared in advance.** Silent model,
   provider, capability, permission, or access-service substitution can change
   the security and quality contract. Failure to find an approved route must
   stop with a closed reason rather than improvise.
7. **Canonical digests turn drift into a checkable fact.** Equivalent ordering
   should hash identically; a material change should not. Each evaluation must
   bind the policy and host/access snapshots it actually used.
8. **Negative fixtures are the executable specification.** The useful matrix
   includes ambiguity, missing capability, no eligible route, widened
   permission, access substitution, forbidden fallback, stale policy, reused
   escalation, and insufficient verifier independence—not only a happy path.
9. **Discovery itself needs containment.** Exact allowlisted argv, no shell,
   no ambient credentials or user configuration, bounded output, fixed
   timeout, normalized metadata, freshness, and a digest are part of the
   discovery contract. Raw command output should not become a portable
   artifact or an accidental secret store.

## Framework changes derived from the lessons

- `clientagent.routing` now provides the portable route-policy projection,
  canonical digest, immutable host-profile and proposal records, closed result
  vocabulary, and deterministic three-role coherence validator.
- `clientagent.machine_inventory` now provides credential-free exact-command
  discovery with bounded transient output, normalized version/model metadata,
  timestamps, freshness, and an integrity digest.
- Portable tests cover the happy path and the principal fail-closed cases.

These primitives intentionally do not perform provider discovery, decide
eligibility, choose routes, dispatch work, advance lifecycle state, or grant
approval. Those remain server-owned deployment responsibilities.

## Remaining integration work

The next framework step is lineage: bind the route-policy, inventory, profile,
access-service, and coherence digests to the job and carry them through
verification evidence, review artifacts, and maintainer handoff. Each real
deployment must separately certify its collector and adapters on the intended
host.
