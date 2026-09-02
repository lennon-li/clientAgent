"""Server-owned runtime capability attestation for governed deployments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .governance import GovernancePolicy


class RuntimeAdapterError(ValueError):
    """Raised when a runtime cannot prove that it is within policy."""


@dataclass(frozen=True)
class RuntimeCapabilitySnapshot:
    """Facts measured by a server-owned adapter at deployment startup."""

    cli_agent: str
    cli_version: str
    adapter_id: str
    adapter_version: str
    policy_mapping_version: str
    effective_capabilities: Mapping[str, Any]
    enforced_controls: frozenset[str] = frozenset()
    unsupported_controls: frozenset[str] = frozenset()
    unapproved_configuration_detected: bool = False

    def __post_init__(self) -> None:
        for field in (
            "cli_agent", "cli_version", "adapter_id", "adapter_version",
            "policy_mapping_version",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.effective_capabilities, Mapping):
            raise TypeError("effective_capabilities must be a mapping")
        if not isinstance(self.enforced_controls, frozenset):
            raise TypeError("enforced_controls must be a frozenset")
        if not isinstance(self.unsupported_controls, frozenset):
            raise TypeError("unsupported_controls must be a frozenset")
        if not isinstance(self.unapproved_configuration_detected, bool):
            raise TypeError("unapproved_configuration_detected must be boolean")


@dataclass(frozen=True)
class RuntimeAttestation:
    """Immutable identity and effective-capability evidence for one startup."""

    cli_agent: str
    cli_version: str
    adapter_id: str
    adapter_version: str
    policy_mapping_version: str
    effective_capabilities_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "cli_agent": self.cli_agent,
            "cli_version": self.cli_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "policy_mapping_version": self.policy_mapping_version,
            "effective_capabilities_hash": self.effective_capabilities_hash,
        }


_FALSE_GUARDRAILS = (
    "capabilities.resource_access.cross_project_access",
    "capabilities.operations.arbitrary_command_execution",
    "capabilities.tools.unlisted_tools_allowed",
    "capabilities.network.unlisted_destinations_allowed",
    "capabilities.data.row_level_access",
    "capabilities.data.export_allowed",
    "capabilities.credentials.direct_access_allowed",
    "capabilities.credentials.credential_export_allowed",
    "workspace_policy.reuse_across_users",
    "workspace_policy.automatic_destructive_cleanup",
    "verification.agent_self_report_is_evidence",
    "artifact_policy.active_content_allowed",
    "artifact_policy.sensitive_data_allowed",
    "review_policy.user_acceptance_authorizes_integration",
    "review_policy.user_acceptance_authorizes_release",
    "review_policy.user_acceptance_authorizes_deployment",
    "failure_policy.automatic_retry_allowed",
    "failure_policy.automatic_reset_allowed",
    "failure_policy.automatic_evidence_deletion_allowed",
)

_TRUE_GUARDRAILS = (
    "workspace_policy.isolation_required",
    "workspace_policy.dedicated_to_agent_and_project",
    "workspace_policy.preserve_on_uncertainty",
    "workspace_policy.integrity_check_before_trusted_action",
    "verification.required",
    "verification.independent_from_agent",
    "verification.verifies_immutable_candidate",
    "verification.evidence_required",
    "verification.artifact_requires_verification",
    "artifact_policy.immutable_versions",
    "artifact_policy.content_hash_required",
    "artifact_policy.parent_revision_link_required",
    "artifact_policy.content_inspection_required",
    "artifact_policy.compare_with_previous_revision",
    "review_policy.required",
    "review_policy.feedback_bound_to_artifact_version",
    "review_policy.revision_requires_new_verification",
    "review_policy.revision_requires_new_artifact",
    "failure_policy.fail_closed",
    "failure_policy.resume_requires_maintainer",
)

_ALLOWLISTS = (
    "capabilities.resource_access.readable",
    "capabilities.resource_access.writable",
    "capabilities.operations.allowed_action_ids",
    "capabilities.tools.allowed_tool_ids",
    "capabilities.network.allowed_destinations",
    "capabilities.network.allowed_methods",
    "capabilities.data.allowed_sources",
    "capabilities.credentials.brokered_credential_ids",
    "models.allowed",
)


def _at_path(data: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise RuntimeAdapterError(f"missing capability field: {dotted_path}")
        current = current[part]
    return current


def _capability_hash(capabilities: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(capabilities), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RuntimeEnforcementAdapter:
    """Attest a measured runtime against one approved governance policy.

    The probe is supplied by the server/deployment host. User, project, and
    agent-authored content cannot provide or replace the measured snapshot.
    """

    def __init__(self, probe: Callable[[], RuntimeCapabilitySnapshot]) -> None:
        if not callable(probe):
            raise TypeError("probe must be callable")
        self._probe = probe

    def attest(self, policy: GovernancePolicy) -> RuntimeAttestation:
        try:
            snapshot = self._probe()
        except Exception as exc:  # pragma: no cover - exact probe failures vary by host
            raise RuntimeAdapterError("runtime capability probe failed") from exc
        if not isinstance(snapshot, RuntimeCapabilitySnapshot):
            raise RuntimeAdapterError("runtime probe did not return a capability snapshot")

        adapter = policy.raw["execution_adapter"]
        identity_checks = (
            ("cli_agent", adapter["cli_agent"]),
            ("cli_version", adapter["cli_version"]),
            ("adapter_id", adapter["adapter_id"]),
            ("adapter_version", adapter["adapter_version"]),
            ("policy_mapping_version", adapter["policy_mapping_version"]),
        )
        reasons: list[str] = []
        for field, expected in identity_checks:
            actual = getattr(snapshot, field)
            if actual != expected:
                reasons.append(f"{field} mismatch")
        if snapshot.unapproved_configuration_detected:
            reasons.append("unapproved configuration can widen runtime behavior")

        required_external = set(adapter["external_controls_required"])
        unsupported = set(adapter["unsupported_required_controls"]) | set(snapshot.unsupported_controls)
        if unsupported & required_external:
            reasons.append("a required external control is unsupported")
        if not required_external.issubset(snapshot.enforced_controls):
            reasons.append("required external controls are not all enforced")

        for path in _FALSE_GUARDRAILS:
            expected = _at_path(policy.raw, path)
            actual = snapshot.effective_capabilities.get(path)
            if actual is None:
                reasons.append(f"effective capability is missing: {path}")
            elif expected is False and actual is not False:
                reasons.append(f"effective capability is broader: {path}")
        for path in _TRUE_GUARDRAILS:
            expected = _at_path(policy.raw, path)
            actual = snapshot.effective_capabilities.get(path)
            if actual is None:
                reasons.append(f"effective capability is missing: {path}")
            elif expected is True and actual is not True:
                reasons.append(f"required control is ineffective: {path}")
        for path in _ALLOWLISTS:
            expected = _at_path(policy.raw, path)
            actual = snapshot.effective_capabilities.get(path)
            if actual is None:
                reasons.append(f"effective allowlist is missing: {path}")
            elif not isinstance(actual, (list, tuple, set, frozenset)):
                reasons.append(f"effective allowlist is malformed: {path}")
            elif not isinstance(expected, (list, tuple, set, frozenset)):
                reasons.append(f"approved allowlist is malformed: {path}")
            elif not set(actual).issubset(set(expected)):
                reasons.append(f"effective allowlist is broader: {path}")

        if reasons:
            raise RuntimeAdapterError("runtime is not within governance policy: " + "; ".join(reasons))
        return RuntimeAttestation(
            cli_agent=snapshot.cli_agent,
            cli_version=snapshot.cli_version,
            adapter_id=snapshot.adapter_id,
            adapter_version=snapshot.adapter_version,
            policy_mapping_version=snapshot.policy_mapping_version,
            effective_capabilities_hash=_capability_hash(snapshot.effective_capabilities),
        )


__all__ = [
    "RuntimeAdapterError",
    "RuntimeAttestation",
    "RuntimeCapabilitySnapshot",
    "RuntimeEnforcementAdapter",
]
