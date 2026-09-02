from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clientagent.deployment import DeploymentAdapter, DeploymentConfig
from clientagent.governance import GovernanceError, load_governance, require_startable
from clientagent.runtime import RuntimeCapabilitySnapshot, RuntimeEnforcementAdapter

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "specialized-agent-governance.yaml"


def _replace_placeholders(value):
    if isinstance(value, dict):
        return {key: _replace_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item) for item in value]
    return "configured" if value == "REPLACE_ME" else value


def _write_policy(path: Path, *, approved: bool = False) -> None:
    data = _replace_placeholders(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")))
    if approved:
        data["metadata"].update(status="approved", enabled=True, approved_by="governance-owner",
                                 approved_at="2026-09-02T00:00:00Z", review_due_at="2027-09-02T00:00:00Z")
        data["setup_record"].update(setup_session_id="session-1", generated_at="2026-09-02T00:00:00Z",
                                     confirmed_by_developer_at="2026-09-02T00:00:00Z", responses_complete=True)
        data["execution_adapter"]["effective_capabilities_verified"] = True
        data["approval"].update(developer_attestation=True, governance_review_complete=True,
                                 effective_permissions_verified=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    if approved:
        policy = load_governance(path)
        data["approval"]["approved_policy_hash"] = policy.policy_hash
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _runtime_adapter(policy):
    paths = (
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
    effective = {}
    for path in paths:
        value = policy.raw
        for part in path.split("."):
            value = value[part]
        effective[path] = value
    adapter = policy.raw["execution_adapter"]
    snapshot = RuntimeCapabilitySnapshot(
        cli_agent=adapter["cli_agent"], cli_version=adapter["cli_version"],
        adapter_id=adapter["adapter_id"], adapter_version=adapter["adapter_version"],
        policy_mapping_version=adapter["policy_mapping_version"],
        effective_capabilities=effective,
    )
    return RuntimeEnforcementAdapter(lambda: snapshot)


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(GovernanceError, match="missing"):
        load_governance(tmp_path / "governance.yaml")


def test_template_placeholders_are_rejected():
    with pytest.raises(GovernanceError, match="concrete"):
        load_governance(TEMPLATE)


def test_structurally_valid_draft_cannot_start(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    with pytest.raises(GovernanceError, match="not approved"):
        require_startable(load_governance(path))


def test_approved_policy_requires_matching_hash(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    policy = load_governance(path)
    assert require_startable(policy).policy_hash == policy.policy_hash

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["specialization"]["purpose"] = "tampered"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(GovernanceError, match="hash"):
        require_startable(load_governance(path))


def test_deployment_adapter_attaches_policy_identity_to_execution(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    policy = load_governance(path)
    adapter = DeploymentAdapter(
        DeploymentConfig(deployment_id="deployment-1", project_id="configured", governance_file=path,
                         runtime_adapter=_runtime_adapter(policy))
    )

    with pytest.raises(RuntimeError, match="not passed"):
        adapter.execute(lambda _: "must-not-run")

    attestation = adapter.start()
    seen = adapter.execute(lambda identity: identity.as_dict())
    assert seen["deployment_id"] == "deployment-1"
    assert seen["project_id"] == "configured"
    assert seen["policy_hash"] == attestation.policy_hash
    assert adapter.evidence("startup")["governance"]["policy_hash"] == attestation.policy_hash


def test_deployment_requires_a_server_owned_runtime_adapter(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    adapter = DeploymentAdapter(
        DeploymentConfig(deployment_id="deployment-1", project_id="configured", governance_file=path)
    )

    with pytest.raises(RuntimeError, match="runtime enforcement adapter"):
        adapter.start()


def test_deployment_adapter_refuses_project_identity_mismatch(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    adapter = DeploymentAdapter(
        DeploymentConfig(deployment_id="deployment-1", project_id="other", governance_file=path)
    )
    with pytest.raises(ValueError, match="project_id"):
        adapter.start()


def test_failed_revalidation_revokes_an_earlier_attestation(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    policy = load_governance(path)
    adapter = DeploymentAdapter(
        DeploymentConfig(deployment_id="deployment-1", project_id="configured", governance_file=path,
                         runtime_adapter=_runtime_adapter(policy))
    )
    adapter.start()

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["metadata"]["enabled"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(GovernanceError, match="enabled"):
        adapter.start()
    assert adapter.started is False
    with pytest.raises(RuntimeError, match="not passed"):
        adapter.execute(lambda _: "must-not-run")


def test_startup_rejects_a_broadened_capability_even_when_approved(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path, approved=True)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["capabilities"]["operations"]["arbitrary_command_execution"] = True
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    policy = load_governance(path)
    data["approval"]["approved_policy_hash"] = policy.policy_hash
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(GovernanceError, match="arbitrary command"):
        policy = load_governance(path)
        DeploymentAdapter(
            DeploymentConfig(deployment_id="deployment-1", project_id="configured", governance_file=path,
                             runtime_adapter=_runtime_adapter(policy))
        ).start()
