"""Load and validate the framework's server-owned governance contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

SCHEMA = "clientagent-governance/v1"
_STATUSES = {"draft", "approved", "suspended", "retired"}

_SECTION_KEYS: dict[str, set[str]] = {
    "metadata": {
        "agent_id", "display_name", "project_id", "contract_version", "status", "enabled",
        "developer", "project_owner", "governance_owner", "approved_by", "approved_at",
        "review_due_at", "description",
    },
    "setup_record": {
        "created_by_role", "setup_session_id", "questionnaire_version", "generated_at",
        "confirmed_by_developer_at", "responses_complete", "unresolved_questions",
    },
    "specialization": {
        "purpose", "intended_users", "supported_requests", "out_of_scope_requests",
        "expected_artifacts", "escalation_owner", "escalation_conditions",
    },
    "developer_declaration": {"agent_may", "agent_must", "agent_must_not", "agent_must_escalate_when"},
    "models": {"allowed", "default", "fallback_allowed", "fallback_models", "model_switch_requires_approval"},
    "execution_adapter": {
        "cli_agent", "cli_version", "adapter_id", "adapter_version", "policy_mapping_version",
        "startup_attestation_required", "ignore_unapproved_cli_configuration",
        "unsupported_required_controls", "external_controls_required",
        "effective_capabilities_verified", "refuse_when_policy_cannot_be_enforced",
    },
    "token_and_cost_budgets": {
        "max_input_tokens_per_call", "max_output_tokens_per_call", "max_turns_per_job",
        "max_total_tokens_per_job", "max_total_tokens_per_revision", "max_total_tokens_per_review_cycle",
        "max_cost_per_job", "max_cost_per_review_cycle", "currency", "preflight_check_required",
        "stop_on_limit", "allow_runtime_increase", "usage_evidence_required",
    },
    "context_policy": {
        "allowed_sources", "prohibited_sources", "max_system_instruction_tokens",
        "max_conversation_history_tokens", "max_project_context_tokens", "max_retrieved_context_tokens",
        "max_tool_result_tokens", "history_strategy", "preserve_review_feedback",
        "preserve_artifact_lineage", "cross_project_context_allowed",
    },
    "workload_limits": {
        "max_wall_time_seconds_per_job", "max_tool_calls_per_job", "max_retries_per_step",
        "max_concurrent_jobs_per_user", "max_queued_jobs_per_user", "max_revisions_per_request",
        "max_workspace_bytes", "max_files_changed", "max_artifact_bytes",
    },
    "capabilities": {"resource_access", "operations", "tools", "network", "data", "credentials"},
    "workspace_policy": {
        "isolation_required", "dedicated_to_agent_and_project", "reuse_across_users",
        "preserve_on_uncertainty", "automatic_destructive_cleanup", "integrity_check_before_trusted_action",
    },
    "verification": {
        "required", "independent_from_agent", "verifies_immutable_candidate", "mandatory_check_ids",
        "optional_check_ids", "acceptance_rule", "agent_self_report_is_evidence", "evidence_required",
        "artifact_requires_verification",
    },
    "artifact_policy": {
        "builder_id", "allowed_types", "allowed_formats", "prohibited_content", "immutable_versions",
        "content_hash_required", "parent_revision_link_required", "active_content_allowed",
        "sensitive_data_allowed", "content_inspection_required", "compare_with_previous_revision",
    },
    "review_policy": {
        "required", "allowed_reviewer_roles", "allowed_actions", "feedback_bound_to_artifact_version",
        "revision_requires_new_verification", "revision_requires_new_artifact",
        "user_acceptance_authorizes_integration", "user_acceptance_authorizes_release",
        "user_acceptance_authorizes_deployment",
    },
    "approval_gates": {
        "scope_change", "new_capability", "budget_change", "model_change", "data_access_change",
        "artifact_policy_change", "integration", "release", "deployment",
    },
    "audit_policy": {
        "append_only_events", "record_authentication", "record_authorization", "record_scope_decisions",
        "record_model_and_token_usage", "record_tool_usage", "record_verification",
        "record_artifact_versions", "record_review_actions", "record_approvals_and_overrides",
        "redact_credentials", "minimize_sensitive_content", "retention_days",
    },
    "failure_policy": {
        "fail_closed", "needs_attention_on", "automatic_retry_allowed", "automatic_reset_allowed",
        "automatic_evidence_deletion_allowed", "resume_requires_maintainer",
    },
    "incident_policy": {
        "emergency_stop_available", "incident_owner", "preserve_evidence_on_stop",
        "credential_revocation_supported", "invalidate_artifact_access_supported",
    },
    "approval": {
        "developer_attestation", "governance_review_complete", "effective_permissions_verified",
        "approved_policy_hash",
    },
}

_ROOT_KEYS = {"schema", "template_version", *(_SECTION_KEYS.keys())}


class GovernanceError(ValueError):
    """Raised when a governance document is missing, invalid, or unsafe."""


@dataclass(frozen=True)
class GovernancePolicy:
    path: Path
    raw: dict[str, Any]
    policy_hash: str

    @property
    def agent_id(self) -> str:
        return self.raw["metadata"]["agent_id"]

    @property
    def contract_version(self) -> str:
        return self.raw["metadata"]["contract_version"]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GovernanceError(f"{name} must be a mapping")
    return value


def _check_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing:
        raise GovernanceError(f"{name} missing required fields: {sorted(missing)}")
    if extra:
        raise GovernanceError(f"{name} has unsupported fields: {sorted(extra)}")


def _text(value: Any, name: str, nullable: bool = False, *, allow_placeholder: bool = False) -> None:
    if nullable and value is None:
        return
    if (not isinstance(value, str) or not value.strip()
            or (not allow_placeholder and "REPLACE_ME" in value)):
        raise GovernanceError(f"{name} must be a concrete non-empty string")


def _boolean(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise GovernanceError(f"{name} must be boolean")


def _list_of_strings(value: Any, name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GovernanceError(f"{name} must be a list of strings")


def _validate(data: dict[str, Any], *, allow_placeholders: bool = False) -> None:
    def text(value: Any, name: str, nullable: bool = False) -> None:
        _text(value, name, nullable=nullable, allow_placeholder=allow_placeholders)

    _check_keys(data, _ROOT_KEYS, "governance document")
    if data["schema"] != SCHEMA:
        raise GovernanceError(f"schema must be {SCHEMA!r}")
    text(data["template_version"], "template_version")

    for section, expected in _SECTION_KEYS.items():
        _check_keys(_mapping(data[section], section), expected, section)

    metadata = data["metadata"]
    for field in ("agent_id", "display_name", "project_id", "contract_version", "developer",
                  "project_owner", "governance_owner", "description"):
        text(metadata[field], f"metadata.{field}")
    if metadata["status"] not in _STATUSES:
        raise GovernanceError(f"metadata.status must be one of {sorted(_STATUSES)}")
    _boolean(metadata["enabled"], "metadata.enabled")
    for field in ("approved_by", "approved_at", "review_due_at"):
        text(metadata[field], f"metadata.{field}", nullable=True)

    setup = data["setup_record"]
    text(setup["created_by_role"], "setup_record.created_by_role")
    text(setup["setup_session_id"], "setup_record.setup_session_id", nullable=True)
    text(setup["questionnaire_version"], "setup_record.questionnaire_version")
    for field in ("generated_at", "confirmed_by_developer_at"):
        text(setup[field], f"setup_record.{field}", nullable=True)
    _boolean(setup["responses_complete"], "setup_record.responses_complete")
    _list_of_strings(setup["unresolved_questions"], "setup_record.unresolved_questions")

    specialization = data["specialization"]
    text(specialization["purpose"], "specialization.purpose")
    text(specialization["escalation_owner"], "specialization.escalation_owner")
    for field in ("intended_users", "supported_requests", "out_of_scope_requests",
                  "expected_artifacts", "escalation_conditions"):
        _list_of_strings(specialization[field], f"specialization.{field}")

    declaration = data["developer_declaration"]
    for field in declaration:
        _list_of_strings(declaration[field], f"developer_declaration.{field}")

    adapter = data["execution_adapter"]
    for field in ("cli_agent", "cli_version", "adapter_id", "adapter_version", "policy_mapping_version"):
        text(adapter[field], f"execution_adapter.{field}")
    for field in ("startup_attestation_required", "ignore_unapproved_cli_configuration",
                  "effective_capabilities_verified", "refuse_when_policy_cannot_be_enforced"):
        _boolean(adapter[field], f"execution_adapter.{field}")
    for field in ("unsupported_required_controls", "external_controls_required"):
        _list_of_strings(adapter[field], f"execution_adapter.{field}")

    approval = data["approval"]
    for field in ("developer_attestation", "governance_review_complete", "effective_permissions_verified"):
        _boolean(approval[field], f"approval.{field}")
    text(approval["approved_policy_hash"], "approval.approved_policy_hash", nullable=True)


def validate_draft(data: Mapping[str, Any]) -> None:
    """Validate draft structure while allowing explicit unresolved placeholders."""
    if not isinstance(data, dict):
        raise GovernanceError("governance document must be a mapping")
    try:
        _validate(data, allow_placeholders=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise GovernanceError("governance document is structurally incomplete") from exc


def _hash(data: Mapping[str, Any]) -> str:
    normalized = json.loads(json.dumps(data))
    normalized["approval"]["approved_policy_hash"] = None
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_governance(path: str | Path) -> GovernancePolicy:
    policy_path = Path(path)
    if not policy_path.is_file():
        raise GovernanceError(f"governance file is missing: {policy_path}")
    try:
        data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GovernanceError(f"cannot read governance file: {policy_path}") from exc
    if not isinstance(data, dict):
        raise GovernanceError("governance document must be a mapping")
    try:
        _validate(data)
        digest = _hash(data)
    except GovernanceError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise GovernanceError("governance document is structurally incomplete") from exc
    return GovernancePolicy(policy_path, data, digest)


def require_startable(policy: GovernancePolicy) -> GovernancePolicy:
    """Require every approval and integrity gate before execution begins."""
    metadata = policy.raw["metadata"]
    setup = policy.raw["setup_record"]
    adapter = policy.raw["execution_adapter"]
    approval = policy.raw["approval"]
    capabilities = policy.raw["capabilities"]
    workspace = policy.raw["workspace_policy"]
    verification = policy.raw["verification"]
    artifact = policy.raw["artifact_policy"]
    review = policy.raw["review_policy"]
    failure = policy.raw["failure_policy"]
    reasons: list[str] = []
    if metadata["status"] != "approved":
        reasons.append("metadata.status is not approved")
    if metadata["enabled"] is not True:
        reasons.append("metadata.enabled is false")
    if setup["responses_complete"] is not True or setup["unresolved_questions"]:
        reasons.append("setup responses are incomplete")
    if any(approval[field] is not True for field in (
        "developer_attestation", "governance_review_complete", "effective_permissions_verified",
    )):
        reasons.append("approval gates are incomplete")
    if adapter["unsupported_required_controls"]:
        reasons.append("required controls are unsupported")
    if adapter["effective_capabilities_verified"] is not True:
        reasons.append("effective capabilities are not verified")
    if adapter["startup_attestation_required"] is not True:
        reasons.append("startup attestation is disabled")
    if adapter["ignore_unapproved_cli_configuration"] is not True:
        reasons.append("unapproved CLI configuration is not ignored")
    if adapter["refuse_when_policy_cannot_be_enforced"] is not True:
        reasons.append("unsupported policy enforcement is not refused")
    if capabilities["resource_access"]["cross_project_access"] is not False:
        reasons.append("cross-project access is enabled")
    if capabilities["operations"]["arbitrary_command_execution"] is not False:
        reasons.append("arbitrary command execution is enabled")
    if capabilities["tools"]["unlisted_tools_allowed"] is not False:
        reasons.append("unlisted tools are allowed")
    if capabilities["network"]["unlisted_destinations_allowed"] is not False:
        reasons.append("unlisted network destinations are allowed")
    if capabilities["credentials"]["direct_access_allowed"] is not False:
        reasons.append("direct credential access is enabled")
    if capabilities["credentials"]["credential_export_allowed"] is not False:
        reasons.append("credential export is enabled")
    for field, label in (
        ("isolation_required", "workspace isolation"),
        ("dedicated_to_agent_and_project", "dedicated workspace"),
        ("preserve_on_uncertainty", "uncertainty preservation"),
        ("integrity_check_before_trusted_action", "trusted-action integrity checks"),
    ):
        if workspace[field] is not True:
            reasons.append(f"{label} is disabled")
    if workspace["automatic_destructive_cleanup"] is not False:
        reasons.append("automatic destructive cleanup is enabled")
    if verification["required"] is not True or verification["independent_from_agent"] is not True:
        reasons.append("independent verification is not required")
    if verification["agent_self_report_is_evidence"] is not False:
        reasons.append("agent self-report is accepted as evidence")
    if artifact["immutable_versions"] is not True or artifact["content_hash_required"] is not True:
        reasons.append("immutable content hashing is not required")
    if review["required"] is not True or review["user_acceptance_authorizes_integration"] is not False:
        reasons.append("review or integration separation is disabled")
    if review["user_acceptance_authorizes_release"] is not False or review["user_acceptance_authorizes_deployment"] is not False:
        reasons.append("user acceptance can authorize release or deployment")
    if failure["fail_closed"] is not True or failure["automatic_retry_allowed"] is not False:
        reasons.append("fail-closed or retry policy is unsafe")
    if failure["automatic_reset_allowed"] is not False or failure["automatic_evidence_deletion_allowed"] is not False:
        reasons.append("automatic reset or evidence deletion is enabled")
    if approval["approved_policy_hash"] != policy.policy_hash:
        reasons.append("approved policy hash does not match document")
    if reasons:
        raise GovernanceError("governance policy is not startable: " + "; ".join(reasons))
    return policy
