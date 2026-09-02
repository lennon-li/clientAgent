"""Structured setup-agent flow for creating disabled governance drafts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .governance import GovernanceError, _hash, validate_draft


class SetupError(ValueError):
    """Raised when setup answers cannot safely be applied to a draft."""


@dataclass(frozen=True)
class SetupDraft:
    """The written draft and the fields that still require explicit answers."""

    path: Path
    raw: dict[str, Any]
    draft_hash: str
    unresolved_fields: tuple[str, ...]
    findings: tuple["SetupFinding", ...] = ()

    @property
    def summary(self) -> dict[str, Any]:
        return effective_policy_summary(self.raw, self.unresolved_fields, self.findings)


@dataclass(frozen=True)
class SetupFinding:
    """A semantic setup issue that must be resolved before approval."""

    code: str
    severity: str
    message: str
    fields: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "fields": list(self.fields),
        }


# These fields are owned by approval/deployment, not by the setup agent.
_SETUP_OWNED_FIELDS = {
    "metadata.status",
    "metadata.enabled",
    "setup_record.created_by_role",
    "setup_record.questionnaire_version",
    "setup_record.responses_complete",
    "setup_record.unresolved_questions",
    "setup_record.confirmed_by_developer_at",
    "approval.developer_attestation",
    "approval.governance_review_complete",
    "approval.effective_permissions_verified",
    "approval.approved_policy_hash",
}

# The setup agent may configure scope, but cannot weaken non-negotiable safety
# controls. A caller must use a later, separately approved policy change for
# any change to these values.
_FALSE_GUARDRAILS = {
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
}

_PROTECTED_DENIALS = {
    "capabilities.operations.prohibited_action_ids": {
        "modify_governance_policy",
        "access_credentials",
        "approve_own_work",
        "impersonate_user",
        "integrate_without_approval",
        "release_without_approval",
        "deploy_without_approval",
    },
}


def _read_template(path: str | Path) -> dict[str, Any]:
    template_path = Path(path)
    if not template_path.is_file():
        raise SetupError(f"setup template is missing: {template_path}")
    try:
        data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SetupError(f"cannot read setup template: {template_path}") from exc
    if not isinstance(data, dict):
        raise SetupError("setup template must be a mapping")
    return data


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    paths: set[str] = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        paths.update(_leaf_paths(child, child_prefix))
    return paths


def _get_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SetupError(f"answer path is not a template field: {path}")
        current = current[part]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current: dict[str, Any] = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            raise SetupError(f"answer path is not a template field: {path}")
        current = child
    current[parts[-1]] = deepcopy(value)


def _unresolved_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            paths.extend(_unresolved_paths(child, child_prefix))
        return paths
    if value == "REPLACE_ME" or (value is None and prefix != "approval.approved_policy_hash"):
        return [prefix]
    return []


def _check_answer(path: str, value: Any, template: Mapping[str, Any]) -> None:
    if path in _SETUP_OWNED_FIELDS or path.startswith("approval."):
        raise SetupError(f"setup agent cannot set protected field: {path}")
    if path in _FALSE_GUARDRAILS and value is not False:
        raise SetupError(f"setup agent cannot weaken safety guardrail: {path}")
    if path in _PROTECTED_DENIALS:
        if not isinstance(value, list) or not _PROTECTED_DENIALS[path].issubset(value):
            raise SetupError(f"setup agent cannot remove protected denials: {path}")
    if value is None:
        raise SetupError(f"answers must be explicit; null is not an answer: {path}")
    if isinstance(value, (dict, list)) and any(isinstance(item, (dict, list)) for item in value if isinstance(value, list)):
        raise SetupError(f"answers must use scalar or flat list values: {path}")
    # This lookup also rejects malformed dotted paths before writing anything.
    _get_path(template, path)


def effective_policy_summary(
    data: Mapping[str, Any], unresolved_fields: tuple[str, ...] | list[str] | None = None,
    findings: tuple[SetupFinding, ...] | list[SetupFinding] | None = None,
) -> dict[str, Any]:
    """Return a plain structured summary without treating prose as permission."""
    capabilities = data["capabilities"]
    operations = capabilities["operations"]
    tools = capabilities["tools"]
    network = capabilities["network"]
    credentials = capabilities["credentials"]
    artifact = data["artifact_policy"]
    review = data["review_policy"]
    unresolved = tuple(unresolved_fields or _unresolved_paths(data))
    return {
        "status": data["metadata"]["status"],
        "enabled": data["metadata"]["enabled"],
        "agent_id": None if data["metadata"]["agent_id"] == "REPLACE_ME" else data["metadata"]["agent_id"],
        "unresolved_fields": list(unresolved),
        "findings": [finding.as_dict() for finding in (findings or analyze_draft(data, include_incomplete=False))],
        "access": {
            "read": list(capabilities["resource_access"]["readable"]),
            "write": list(capabilities["resource_access"]["writable"]),
            "network_destinations": list(network["allowed_destinations"]),
            "tools": list(tools["allowed_tool_ids"]),
            "direct_credentials": credentials["direct_access_allowed"],
        },
        "allowed_actions": list(operations["allowed_action_ids"]),
        "artifacts": {
            "types": list(artifact["allowed_types"]),
            "formats": list(artifact["allowed_formats"]),
            "active_content": artifact["active_content_allowed"],
            "sensitive_data": artifact["sensitive_data_allowed"],
        },
        "approval": {
            "can_approve_own_work": False,
            "can_integrate": review["user_acceptance_authorizes_integration"],
            "can_release": review["user_acceptance_authorizes_release"],
            "can_deploy": review["user_acceptance_authorizes_deployment"],
        },
        "never": [
            "change governance or approval state",
            "access credentials directly or export them",
            "cross project boundaries",
            "execute arbitrary commands",
        ],
    }


def analyze_draft(data: Mapping[str, Any], *, include_incomplete: bool = True) -> tuple[SetupFinding, ...]:
    """Find semantic conflicts and controls requiring an enforcement adapter.

    This analysis never grants a capability. It only reports conditions that
    keep the already-disabled draft from being considered ready for approval.
    """
    try:
        validate_draft(data)
    except GovernanceError as exc:
        raise SetupError(f"cannot analyze invalid draft: {exc}") from exc

    findings: list[SetupFinding] = []
    if include_incomplete:
        unresolved = _unresolved_paths(data)
        if unresolved:
            findings.append(SetupFinding(
                "missing_answers",
                "blocking",
                f"{len(unresolved)} required or ownership fields remain unresolved.",
                tuple(unresolved),
            ))

    specialization = data["specialization"]
    supported = set(specialization["supported_requests"])
    out_of_scope = set(specialization["out_of_scope_requests"])
    if supported & out_of_scope:
        findings.append(SetupFinding(
            "scope_conflict",
            "blocking",
            "A request is listed as both supported and out of scope.",
            ("specialization.supported_requests", "specialization.out_of_scope_requests"),
        ))

    models = data["models"]
    allowed_models = set(models["allowed"])
    if models["default"] is not None and models["default"] not in allowed_models:
        findings.append(SetupFinding(
            "model_conflict",
            "blocking",
            "The default model is not in the approved model list.",
            ("models.allowed", "models.default"),
        ))
    if not models["fallback_allowed"] and models["fallback_models"]:
        findings.append(SetupFinding(
            "fallback_conflict",
            "blocking",
            "Fallback models are listed while fallback use is disabled.",
            ("models.fallback_allowed", "models.fallback_models"),
        ))
    elif not set(models["fallback_models"]).issubset(allowed_models):
        findings.append(SetupFinding(
            "fallback_conflict",
            "blocking",
            "A fallback model is not in the approved model list.",
            ("models.allowed", "models.fallback_models"),
        ))

    capabilities = data["capabilities"]
    resources = capabilities["resource_access"]
    operations = capabilities["operations"]
    tools = capabilities["tools"]
    network = capabilities["network"]
    context = data["context_policy"]
    overlaps = (
        (set(resources["readable"]) & set(resources["prohibited"]),
         "resource_conflict", "A resource is both readable and prohibited.",
         ("capabilities.resource_access.readable", "capabilities.resource_access.prohibited")),
        (set(operations["allowed_action_ids"]) & set(operations["prohibited_action_ids"]),
         "operation_conflict", "An operation is both allowed and prohibited.",
         ("capabilities.operations.allowed_action_ids", "capabilities.operations.prohibited_action_ids")),
        (set(tools["allowed_tool_ids"]) & set(tools["prohibited_tool_ids"]),
         "tool_conflict", "A tool is both allowed and prohibited.",
         ("capabilities.tools.allowed_tool_ids", "capabilities.tools.prohibited_tool_ids")),
        (set(context["allowed_sources"]) & set(context["prohibited_sources"]),
         "context_conflict", "A context source is both allowed and prohibited.",
         ("context_policy.allowed_sources", "context_policy.prohibited_sources")),
    )
    for overlap, code, message, fields in overlaps:
        if overlap:
            findings.append(SetupFinding(code, "blocking", message, fields))

    if not network["enabled"] and (network["allowed_destinations"] or network["allowed_methods"]):
        findings.append(SetupFinding(
            "network_conflict",
            "blocking",
            "Network destinations or methods are configured while network access is disabled.",
            ("capabilities.network.enabled", "capabilities.network.allowed_destinations", "capabilities.network.allowed_methods"),
        ))
    if network["enabled"] and not network["allowed_destinations"]:
        findings.append(SetupFinding(
            "network_scope_missing",
            "blocking",
            "Network access is enabled without an explicit destination allowlist.",
            ("capabilities.network.enabled", "capabilities.network.allowed_destinations"),
        ))

    adapter = data["execution_adapter"]
    if adapter["unsupported_required_controls"]:
        findings.append(SetupFinding(
            "unsupported_control",
            "blocking",
            "The selected adapter cannot enforce one or more required controls.",
            ("execution_adapter.unsupported_required_controls",),
        ))
    if adapter["external_controls_required"]:
        findings.append(SetupFinding(
            "external_control",
            "requires_external_enforcement",
            "One or more controls must be enforced outside the CLI agent before approval.",
            ("execution_adapter.external_controls_required",),
        ))
    return tuple(findings)


def create_draft(
    template_path: str | Path,
    output_path: str | Path,
    answers: Mapping[str, Any],
    *,
    setup_session_id: str | None = None,
    generated_at: str | None = None,
) -> SetupDraft:
    """Apply explicit dotted-field answers and write one disabled draft.

    ``answers`` is deliberately a mapping, not prose. Missing fields remain
    unresolved or retain the template's safe default; no permission is inferred.
    Approval, enablement, and effective-capability attestation stay server-owned.
    """
    if not isinstance(answers, Mapping):
        raise SetupError("answers must be a mapping of dotted field paths to values")
    template = _read_template(template_path)
    validate_draft(template)
    draft = deepcopy(template)
    fields = _leaf_paths(template)
    for path, value in answers.items():
        if not isinstance(path, str) or path not in fields:
            raise SetupError(f"answer path is not a template field: {path!r}")
        _check_answer(path, value, template)
        _set_path(draft, path, value)
        try:
            validate_draft(draft)
        except GovernanceError as exc:
            raise SetupError(f"answer makes the draft invalid at {path}: {exc}") from exc

    # These values are always setup-agent output, never interview answers.
    draft["metadata"]["status"] = "draft"
    draft["metadata"]["enabled"] = False
    draft["setup_record"]["created_by_role"] = "setup_agent"
    draft["setup_record"]["questionnaire_version"] = template["setup_record"]["questionnaire_version"]
    draft["setup_record"]["responses_complete"] = False
    draft["setup_record"]["unresolved_questions"] = _unresolved_paths(draft)
    draft["approval"] = {
        **draft["approval"],
        "developer_attestation": False,
        "governance_review_complete": False,
        "effective_permissions_verified": False,
        "approved_policy_hash": None,
    }
    if setup_session_id is not None:
        if not isinstance(setup_session_id, str) or not setup_session_id.strip():
            raise SetupError("setup_session_id must be a non-empty string")
        draft["setup_record"]["setup_session_id"] = setup_session_id
    if generated_at is not None:
        if not isinstance(generated_at, str) or not generated_at.strip():
            raise SetupError("generated_at must be a non-empty string")
        draft["setup_record"]["generated_at"] = generated_at
    try:
        validate_draft(draft)
    except GovernanceError as exc:
        raise SetupError(f"draft is invalid after setup answers: {exc}") from exc
    findings = analyze_draft(draft)

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"cannot write setup draft: {path}") from exc
    unresolved = tuple(draft["setup_record"]["unresolved_questions"])
    return SetupDraft(path, draft, _hash(draft), unresolved, findings)


__all__ = [
    "SetupDraft", "SetupError", "SetupFinding", "analyze_draft", "create_draft",
    "effective_policy_summary",
]
