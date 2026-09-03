from pathlib import Path

import pytest
import yaml

from clientagent.artifacts import ImmutableArtifactBuilder
from clientagent.governance import load_governance
from clientagent.lifecycle import LifecycleController, LifecycleState
from clientagent.review import (
    MaintainerDecision,
    ReviewAction,
    ReviewError,
    ReviewWorkflow,
)
from clientagent.verification import CandidateVersion, IndependentVerifier


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
    data["verification"]["mandatory_check_ids"] = ["nonempty"]
    data["artifact_policy"].update(builder_id="artifact-builder", allowed_types=["report"],
                                    allowed_formats=["txt"])
    data["workload_limits"]["max_artifact_bytes"] = 1024
    data["execution_adapter"]["effective_capabilities_verified"] = True
    data["approval"].update(developer_attestation=True, governance_review_complete=True,
                             effective_permissions_verified=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    policy = load_governance(path)
    data["approval"]["approved_policy_hash"] = policy.policy_hash
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_governance(path)


def _workflow(policy):
    lifecycle = LifecycleController(
        "request-1", "configured", {"agent_id": policy.agent_id, "project_id": "configured", "policy_hash": policy.policy_hash}
    )
    workflow = ReviewWorkflow(lifecycle)
    common = {"actor_role": "system", "actor_id": "server", "occurred_at": "now"}
    lifecycle.transition(LifecycleState.SCOPED, reason="scoped", **common)
    lifecycle.transition(LifecycleState.WORKING, candidate_id="candidate-1", reason="working", **common)
    lifecycle.transition(LifecycleState.VERIFICATION, reason="verified", **common)
    lifecycle.transition(LifecycleState.ARTIFACT_BUILDING, reason="building", **common)
    return workflow


def _artifact(policy, candidate, *, revision=1, parent_artifact_id=None):
    evidence = IndependentVerifier(
        "verifier-1", "1.0", {"nonempty": lambda content: bool(content.strip())}
    ).verify(candidate, policy, verified_at="now")
    return ImmutableArtifactBuilder().build(
        candidate, evidence, policy,
        artifact_id=f"artifact-{revision}",
        request_id="request-1",
        artifact_type="report",
        artifact_format="txt",
        preview=f"Preview {revision}",
        description=f"Artifact {revision}",
        created_by="artifact-builder",
        created_at="now",
        inspection_passed=True,
        revision=revision,
        parent_artifact_id=parent_artifact_id,
    )


def test_review_actions_bind_to_exact_artifact_and_create_handoff(tmp_path):
    policy = _policy(tmp_path)
    workflow = _workflow(policy)
    candidate = CandidateVersion.create("candidate-1", "result one")
    artifact = _artifact(policy, candidate)
    workflow.publish_artifact(artifact, actor_role="system", actor_id="server", occurred_at="now", reason="ready")

    with pytest.raises(ReviewError, match="substituted"):
        workflow.review(
            ReviewAction.ACCEPT_FOR_MAINTAINER_REVIEW,
            artifact_id=artifact.artifact_id,
            artifact_hash="wrong-hash",
            actor_role="requester", actor_id="user-1", occurred_at="now", comment="accept",
        )

    review = workflow.review(
        ReviewAction.ACCEPT_FOR_MAINTAINER_REVIEW,
        artifact_id=artifact.artifact_id,
        artifact_hash=artifact.content_hash,
        actor_role="requester", actor_id="user-1", occurred_at="now", comment="accept result",
    )
    assert review.artifact_hash == artifact.content_hash
    handoff = workflow.handoff()
    assert handoff.artifact_id == artifact.artifact_id
    assert handoff.verification["passed"] is True
    assert handoff.as_dict()["integration_authorized"] is False

    decision = workflow.decide_as_maintainer(
        MaintainerDecision.APPROVE_FOR_NEXT_MAINTAINER_ACTION,
        artifact_id=artifact.artifact_id,
        artifact_hash=artifact.content_hash,
        actor_role="maintainer", actor_id="maintainer-1", occurred_at="later", reason="reviewed",
    )
    assert decision.decision == MaintainerDecision.APPROVE_FOR_NEXT_MAINTAINER_ACTION
    assert decision.as_dict()["deployment_authorized"] is False


def test_revision_requires_explicit_request_new_candidate_and_parent_artifact(tmp_path):
    policy = _policy(tmp_path)
    workflow = _workflow(policy)
    first = _artifact(policy, CandidateVersion.create("candidate-1", "result one"))
    workflow.publish_artifact(first, actor_role="system", actor_id="server", occurred_at="now", reason="ready")
    workflow.review(
        ReviewAction.REQUEST_REVISION,
        artifact_id=first.artifact_id,
        artifact_hash=first.content_hash,
        actor_role="requester", actor_id="user-1", occurred_at="now", comment="change section two",
    )
    with pytest.raises(ReviewError, match="new candidate"):
        workflow.start_revision("candidate-1", actor_role="agent", actor_id="agent-1", occurred_at="later", reason="retry")

    workflow.start_revision("candidate-2", actor_role="agent", actor_id="agent-1", occurred_at="later", reason="revision")
    workflow.lifecycle_controller.transition(LifecycleState.VERIFICATION, actor_role="system", actor_id="server", occurred_at="later", reason="verified")
    workflow.lifecycle_controller.transition(LifecycleState.ARTIFACT_BUILDING, actor_role="system", actor_id="server", occurred_at="later", reason="building")
    second = _artifact(policy, CandidateVersion.create("candidate-2", "result two"), revision=2, parent_artifact_id=first.artifact_id)
    workflow.publish_artifact(second, actor_role="system", actor_id="server", occurred_at="later", reason="revised ready")

    assert workflow.lifecycle.state == LifecycleState.REVIEW_READY
    assert [item.artifact_id for item in workflow.artifacts] == [first.artifact_id, second.artifact_id]
    assert second.parent_artifact_id == first.artifact_id
    assert workflow.reviews[0].artifact_id == first.artifact_id


def test_review_rejection_preserves_artifact_and_handoff_requires_acceptance(tmp_path):
    policy = _policy(tmp_path)
    workflow = _workflow(policy)
    artifact = _artifact(policy, CandidateVersion.create("candidate-1", "result"))
    workflow.publish_artifact(artifact, actor_role="system", actor_id="server", occurred_at="now", reason="ready")
    workflow.review(
        ReviewAction.REJECT_OR_ABANDON,
        artifact_id=artifact.artifact_id,
        artifact_hash=artifact.content_hash,
        actor_role="reviewer", actor_id="reviewer-1", occurred_at="now", comment="reject",
    )
    assert workflow.artifacts == (artifact,)
    with pytest.raises(ReviewError, match="accepted"):
        workflow.handoff()
