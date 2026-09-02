from pathlib import Path

import pytest
import yaml

from clientagent.governance import GovernanceError, load_governance
from clientagent.setup import SetupError, analyze_draft, create_draft


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "specialized-agent-governance.yaml"


def test_setup_writes_a_disabled_draft_and_keeps_unanswered_permissions_denied(tmp_path):
    output = tmp_path / "bundle" / "governance.yaml"
    draft = create_draft(TEMPLATE, output, {}, setup_session_id="setup-1")
    data = yaml.safe_load(output.read_text(encoding="utf-8"))

    assert draft.path == output
    assert data["metadata"]["status"] == "draft"
    assert data["metadata"]["enabled"] is False
    assert data["setup_record"]["setup_session_id"] == "setup-1"
    assert data["approval"]["effective_permissions_verified"] is False
    assert data["capabilities"]["resource_access"]["readable"] == []
    assert data["capabilities"]["resource_access"]["writable"] == []
    assert data["capabilities"]["network"]["allowed_destinations"] == []
    assert data["capabilities"]["operations"]["allowed_action_ids"] == []
    assert "metadata.agent_id" in draft.unresolved_fields
    assert draft.summary["enabled"] is False
    assert draft.summary["allowed_actions"] == []

    with pytest.raises(GovernanceError, match="concrete"):
        load_governance(output)


def test_setup_applies_only_explicit_structured_answers(tmp_path):
    output = tmp_path / "governance.yaml"
    draft = create_draft(
        TEMPLATE,
        output,
        {
            "metadata.agent_id": "agent-1",
            "metadata.display_name": "Project Agent",
            "metadata.project_id": "project-1",
            "metadata.developer": "developer-1",
            "metadata.project_owner": "owner-1",
            "metadata.governance_owner": "governance-1",
            "metadata.description": "Answers are explicit.",
            "specialization.purpose": "Produce a bounded artifact.",
            "specialization.escalation_owner": "owner-1",
            "capabilities.resource_access.readable": ["project.docs"],
            "capabilities.operations.allowed_action_ids": ["read_project_docs"],
            "capabilities.tools.allowed_tool_ids": ["document_reader"],
        },
    )

    assert draft.raw["metadata"]["agent_id"] == "agent-1"
    assert draft.raw["capabilities"]["resource_access"]["readable"] == ["project.docs"]
    assert draft.raw["capabilities"]["operations"]["allowed_action_ids"] == ["read_project_docs"]
    assert draft.raw["capabilities"]["network"]["allowed_destinations"] == []
    assert draft.raw["capabilities"]["credentials"]["direct_access_allowed"] is False
    assert draft.raw["metadata"]["enabled"] is False
    assert draft.summary["access"]["tools"] == ["document_reader"]


def test_setup_cannot_approve_enable_or_weaken_guardrails(tmp_path):
    with pytest.raises(SetupError, match="protected field"):
        create_draft(TEMPLATE, tmp_path / "one.yaml", {"metadata.enabled": True})
    with pytest.raises(SetupError, match="protected field"):
        create_draft(TEMPLATE, tmp_path / "two.yaml", {"approval.governance_review_complete": True})
    with pytest.raises(SetupError, match="safety guardrail"):
        create_draft(TEMPLATE, tmp_path / "three.yaml", {"capabilities.network.unlisted_destinations_allowed": True})
    with pytest.raises(SetupError, match="protected denials"):
        create_draft(TEMPLATE, tmp_path / "four.yaml", {"capabilities.operations.prohibited_action_ids": []})


def test_setup_rejects_unknown_or_prose_answer_paths(tmp_path):
    with pytest.raises(SetupError, match="template field"):
        create_draft(TEMPLATE, tmp_path / "one.yaml", {"metadata.unknown": "value"})
    with pytest.raises(SetupError, match="template field"):
        create_draft(TEMPLATE, tmp_path / "two.yaml", {"agent_may": "read everything"})


def test_setup_reports_conflicts_and_external_enforcement_requirements(tmp_path):
    output = tmp_path / "governance.yaml"
    draft = create_draft(
        TEMPLATE,
        output,
        {
            "metadata.agent_id": "agent-1",
            "metadata.display_name": "Agent",
            "metadata.project_id": "project-1",
            "metadata.developer": "developer-1",
            "metadata.project_owner": "owner-1",
            "metadata.governance_owner": "governance-1",
            "metadata.description": "Bounded work.",
            "specialization.purpose": "Produce an artifact.",
            "specialization.escalation_owner": "owner-1",
            "models.allowed": ["model-a"],
            "models.default": "model-b",
            "execution_adapter.external_controls_required": ["network-jail"],
        },
    )
    codes = {finding.code for finding in draft.findings}
    assert {"model_conflict", "external_control"}.issubset(codes)
    assert draft.raw["metadata"]["enabled"] is False
    assert any(item["code"] == "model_conflict" for item in draft.summary["findings"])


def test_analyze_draft_reports_network_scope_conflict(tmp_path):
    output = tmp_path / "governance.yaml"
    draft = create_draft(
        TEMPLATE,
        output,
        {
            "metadata.agent_id": "agent-1",
            "metadata.display_name": "Agent",
            "metadata.project_id": "project-1",
            "metadata.developer": "developer-1",
            "metadata.project_owner": "owner-1",
            "metadata.governance_owner": "governance-1",
            "metadata.description": "Bounded work.",
            "specialization.purpose": "Produce an artifact.",
            "specialization.escalation_owner": "owner-1",
            "capabilities.network.allowed_destinations": ["api.example.test"],
        },
    )
    assert any(finding.code == "network_conflict" for finding in analyze_draft(draft.raw))
