from pathlib import Path

import pytest
import yaml

from clientagent.governance import load_governance
from clientagent.runtime import (
    RuntimeAdapterError,
    RuntimeCapabilitySnapshot,
    RuntimeEnforcementAdapter,
)


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "specialized-agent-governance.yaml"


def _replace_placeholders(value):
    if isinstance(value, dict):
        return {key: _replace_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item) for item in value]
    return "configured" if value == "REPLACE_ME" else value


def _write_policy(path: Path):
    data = _replace_placeholders(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")))
    data["metadata"].update(status="approved", enabled=True, approved_by="governance-owner",
                             approved_at="2026-09-02T00:00:00Z", review_due_at="2027-09-02T00:00:00Z")
    data["setup_record"].update(setup_session_id="session-1", generated_at="2026-09-02T00:00:00Z",
                                 confirmed_by_developer_at="2026-09-02T00:00:00Z", responses_complete=True)
    data["execution_adapter"]["effective_capabilities_verified"] = True
    data["approval"].update(developer_attestation=True, governance_review_complete=True,
                             effective_permissions_verified=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    policy = load_governance(path)
    data["approval"]["approved_policy_hash"] = policy.policy_hash
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


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


def _at_path(data, dotted_path):
    current = data
    for part in dotted_path.split("."):
        current = current[part]
    return current


def _snapshot(policy, **changes):
    adapter = policy.raw["execution_adapter"]
    effective = {
        path: _at_path(policy.raw, path)
        for path in (*_FALSE_GUARDRAILS, *_TRUE_GUARDRAILS, *_ALLOWLISTS)
    }
    effective.update(changes.pop("effective_capabilities", {}))
    values = {
        "cli_agent": adapter["cli_agent"],
        "cli_version": adapter["cli_version"],
        "adapter_id": adapter["adapter_id"],
        "adapter_version": adapter["adapter_version"],
        "policy_mapping_version": adapter["policy_mapping_version"],
        "effective_capabilities": effective,
    }
    values.update(changes)
    return RuntimeCapabilitySnapshot(**values)


def test_runtime_adapter_attests_exact_identity_and_effective_capabilities(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    policy = load_governance(path)

    attestation = RuntimeEnforcementAdapter(lambda: _snapshot(policy)).attest(policy)
    assert attestation.cli_agent == "configured"
    assert attestation.adapter_id == "configured"
    assert len(attestation.effective_capabilities_hash) == 64


def test_runtime_adapter_rejects_identity_mismatch(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    policy = load_governance(path)
    snapshot = _snapshot(policy, cli_version="wrong-version")

    with pytest.raises(RuntimeAdapterError, match="cli_version mismatch"):
        RuntimeEnforcementAdapter(lambda: snapshot).attest(policy)


def test_runtime_adapter_rejects_missing_or_broader_effective_capability(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    policy = load_governance(path)
    missing = _snapshot(policy)
    missing.effective_capabilities.pop("capabilities.operations.arbitrary_command_execution")
    with pytest.raises(RuntimeAdapterError, match="missing"):
        RuntimeEnforcementAdapter(lambda: missing).attest(policy)

    broader = _snapshot(policy, effective_capabilities={
        "capabilities.resource_access.readable": ["unapproved-resource"],
    })
    with pytest.raises(RuntimeAdapterError, match="broader"):
        RuntimeEnforcementAdapter(lambda: broader).attest(policy)


def test_runtime_adapter_requires_external_controls_and_clean_configuration(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    policy = load_governance(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["execution_adapter"]["external_controls_required"] = ["workspace-jail"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    policy = load_governance(path)

    missing_control = _snapshot(policy)
    with pytest.raises(RuntimeAdapterError, match="not all enforced"):
        RuntimeEnforcementAdapter(lambda: missing_control).attest(policy)

    unapproved = _snapshot(policy, enforced_controls=frozenset({"workspace-jail"}),
                           unapproved_configuration_detected=True)
    with pytest.raises(RuntimeAdapterError, match="unapproved configuration"):
        RuntimeEnforcementAdapter(lambda: unapproved).attest(policy)
