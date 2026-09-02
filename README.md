# clientAgent

`clientAgent` is a framework for deploying governed, project-specific AI
agents. It turns a request into bounded candidate work, independent evidence,
and an immutable artifact for human review.

The framework is artifact-centered, not a general coding-agent or autonomous
deployment system. Each deployment is bound to one approved project contract;
users and agents cannot select projects, widen permissions, integrate changes,
release, or deploy.

## Repository layout

- `templates/` — governance contract and setup-agent questionnaire.
- `TODO.md` — product definition, requirements, and milestone backlog.
- `clientagent/` — framework runtime modules (under construction).
- `tests/` — portable framework tests.

## Core lifecycle

```text
request -> bounded work -> independent verification -> review artifact
        -> user feedback -> revised artifact -> user acceptance
        -> separate maintainer decision
```

Configuration, policy, credentials, authorization, sensitive resources, and
release destinations remain outside user and agent control.
