from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from clientagent.governance import GovernanceError, load_governance, require_startable

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
