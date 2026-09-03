from pathlib import Path

import pytest
import yaml

from clientagent.capability_matrix import (
    CapabilityMapping,
    CapabilityMatrix,
    CapabilityMatrixError,
    EnforcementKind,
)
from clientagent.governance import load_governance


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "specialized-agent-governance.yaml"


def _replace_placeholders(value):
    if isinstance(value, dict):
        return {key: _replace_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item) for item in value]
    return "configured" if value == "REPLACE_ME" else value


def _policy(tmp_path):
    path = tmp_path / "governance.yaml"
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
    return load_governance(path)


def _matrix(policy, mappings):
    adapter = policy.raw["execution_adapter"]
    return CapabilityMatrix(
        cli_agent=adapter["cli_agent"],
        cli_version=adapter["cli_version"],
        adapter_id=adapter["adapter_id"],
        adapter_version=adapter["adapter_version"],
        policy_mapping_version=adapter["policy_mapping_version"],
        mappings=tuple(mappings),
    )


def test_capability_matrix_certifies_native_and_external_controls(tmp_path):
    policy = _policy(tmp_path)
    fields = (
        "capabilities.operations.arbitrary_command_execution",
        "capabilities.network.allowed_destinations",
    )
    matrix = _matrix(policy, (
        CapabilityMapping(fields[0], EnforcementKind.NATIVE, "cli.commands.allowlist"),
        CapabilityMapping(fields[1], EnforcementKind.EXTERNAL, "network.egress.jail"),
    ))

    certification = matrix.certify(
        policy, required_fields=fields, certified_at="2026-09-02T00:00:00Z", certified_by="certifier-1"
    )
    assert certification.policy_hash == policy.policy_hash
    assert certification.policy_mapping_version == "configured"
    assert len(certification.matrix_hash) == 64


def test_capability_matrix_rejects_missing_unsupported_or_unknown_mappings(tmp_path):
    policy = _policy(tmp_path)
    with pytest.raises(CapabilityMatrixError, match="no enforcement mapping"):
        _matrix(policy, (CapabilityMapping("models.allowed", "native", "models"),)).certify(
            policy, required_fields=("models.default",), certified_at="now", certified_by="certifier"
        )

    with pytest.raises(CapabilityMatrixError, match="unsupported"):
        _matrix(policy, (CapabilityMapping("models.default", "unsupported", "none"),)).certify(
            policy, required_fields=("models.default",), certified_at="now", certified_by="certifier"
        )

    with pytest.raises(CapabilityMatrixError, match="not in the governance"):
        _matrix(policy, (CapabilityMapping("unknown.field", "native", "unknown"),)).certify(
            policy, required_fields=("unknown.field",), certified_at="now", certified_by="certifier"
        )


def test_capability_matrix_rejects_identity_mismatch_and_prompt_only_kind(tmp_path):
    with pytest.raises(ValueError, match="native, external, or unsupported"):
        CapabilityMapping("models.default", "prompt", "instructions")

    policy = _policy(tmp_path)
    adapter = policy.raw["execution_adapter"]
    matrix = CapabilityMatrix(
        cli_agent=adapter["cli_agent"],
        cli_version="wrong-version",
        adapter_id=adapter["adapter_id"],
        adapter_version=adapter["adapter_version"],
        policy_mapping_version=adapter["policy_mapping_version"],
        mappings=(CapabilityMapping("models.default", "native", "models"),),
    )
    with pytest.raises(CapabilityMatrixError, match="cli_version mismatch"):
        matrix.certify(policy, required_fields=("models.default",), certified_at="now", certified_by="certifier")
