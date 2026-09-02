"""Server-owned deployment boundary for governed agent execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .governance import GovernancePolicy, load_governance, require_startable

T = TypeVar("T")


@dataclass(frozen=True)
class DeploymentConfig:
    """Immutable, server-supplied identity and policy location."""

    deployment_id: str
    project_id: str
    governance_file: Path

    def __post_init__(self) -> None:
        for field in ("deployment_id", "project_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.governance_file, Path):
            raise TypeError("governance_file must be a pathlib.Path")


@dataclass(frozen=True)
class PolicyAttestation:
    """Policy identity attached to every execution/evidence record."""

    deployment_id: str
    project_id: str
    agent_id: str
    contract_version: str
    template_version: str
    policy_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "deployment_id": self.deployment_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "contract_version": self.contract_version,
            "template_version": self.template_version,
            "policy_hash": self.policy_hash,
        }


class DeploymentAdapter:
    """Gate execution on a fresh, approved governance policy.

    The adapter owns the startup transition. Callers cannot provide policy data
    to ``start`` or ``execute``; they can only supply the server-owned config
    and an operation to run after the policy gate passes.
    """

    def __init__(self, config: DeploymentConfig) -> None:
        self._config = config
        self._policy: GovernancePolicy | None = None
        self._attestation: PolicyAttestation | None = None

    @property
    def started(self) -> bool:
        return self._attestation is not None

    @property
    def attestation(self) -> PolicyAttestation:
        if self._attestation is None:
            raise RuntimeError("deployment has not passed governance startup")
        return self._attestation

    def start(self) -> PolicyAttestation:
        """Load and revalidate policy before any execution is permitted."""
        # A failed revalidation must revoke any earlier startup state. Keeping
        # the old attestation would allow execution under a stale policy.
        self._policy = None
        self._attestation = None
        policy = require_startable(load_governance(self._config.governance_file))
        declared_project = policy.raw["metadata"]["project_id"]
        if declared_project != self._config.project_id:
            raise ValueError(
                "governance project_id does not match deployment config: "
                f"{declared_project!r} != {self._config.project_id!r}"
            )
        self._policy = policy
        self._attestation = PolicyAttestation(
            deployment_id=self._config.deployment_id,
            project_id=self._config.project_id,
            agent_id=policy.agent_id,
            contract_version=policy.contract_version,
            template_version=policy.raw["template_version"],
            policy_hash=policy.policy_hash,
        )
        return self._attestation

    def evidence(self, event: str, **details: Any) -> dict[str, Any]:
        """Return an evidence record carrying the immutable policy identity."""
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must be a non-empty string")
        return {
            "event": event,
            "governance": self.attestation.as_dict(),
            "details": dict(details),
        }

    def execute(self, operation: Callable[[PolicyAttestation], T]) -> T:
        """Run an operation only after successful governance startup."""
        if not callable(operation):
            raise TypeError("operation must be callable")
        return operation(self.attestation)


__all__ = ["DeploymentAdapter", "DeploymentConfig", "PolicyAttestation"]
