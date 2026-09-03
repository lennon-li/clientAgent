import pytest

from clientagent.lifecycle import LifecycleController, LifecycleError, LifecycleState


IDENTITY = {"project_id": "project-1", "policy_hash": "hash-1"}


def _controller():
    return LifecycleController("request-1", "project-1", IDENTITY)


def _advance_to_review(controller):
    common = {"actor_role": "system", "actor_id": "server", "occurred_at": "2026-09-02T00:00:00Z"}
    controller.transition(LifecycleState.SCOPED, reason="request accepted", **common)
    controller.transition(LifecycleState.WORKING, candidate_id="candidate-1", reason="work started", **common)
    controller.transition(LifecycleState.VERIFICATION, reason="candidate ready", **common)
    controller.transition(LifecycleState.ARTIFACT_BUILDING, reason="checks passed", **common)
    return controller.transition(
        LifecycleState.REVIEW_READY, artifact_id="artifact-1", reason="artifact built", **common
    )


def test_lifecycle_requires_ordered_states_and_records_history():
    controller = _controller()
    with pytest.raises(LifecycleError, match="cannot transition"):
        controller.transition(
            LifecycleState.WORKING,
            actor_role="system", actor_id="server", occurred_at="now", reason="skip scope",
            candidate_id="candidate-1",
        )

    snapshot = _advance_to_review(controller)
    assert snapshot.state == LifecycleState.REVIEW_READY
    assert snapshot.candidate_id == "candidate-1"
    assert snapshot.artifact_id == "artifact-1"
    assert len(snapshot.history) == 5
    assert snapshot.history[0].to_state == LifecycleState.SCOPED
    assert snapshot.as_dict()["governance"] == IDENTITY


def test_review_cannot_be_edited_without_an_explicit_revision_request():
    controller = _controller()
    _advance_to_review(controller)
    with pytest.raises(LifecycleError, match="cannot transition"):
        controller.transition(
            LifecycleState.WORKING,
            actor_role="agent", actor_id="agent-1", occurred_at="now", reason="edit in review",
        )

    controller.transition(
        LifecycleState.REVISION_REQUESTED,
        actor_role="requester", actor_id="user-1", occurred_at="now", reason="change section two",
    )
    revised = controller.transition(
        LifecycleState.WORKING,
        actor_role="agent", actor_id="agent-1", occurred_at="later", reason="revision started",
        candidate_id="candidate-2",
    )
    assert revised.revision == 1
    assert revised.candidate_id == "candidate-2"
    assert revised.artifact_id == "artifact-1"


def test_review_decisions_require_an_artifact_and_review_role():
    incomplete = _controller()
    incomplete.transition(
        LifecycleState.SCOPED,
        actor_role="system", actor_id="server", occurred_at="now", reason="scoped",
    )
    with pytest.raises(LifecycleError, match="candidate_id"):
        incomplete.transition(
            LifecycleState.WORKING,
            actor_role="system", actor_id="server", occurred_at="now", reason="work",
        )

    controller = _controller()
    _advance_to_review(controller)
    with pytest.raises(LifecycleError, match="review decisions"):
        controller.transition(
            LifecycleState.ACCEPTED,
            actor_role="agent", actor_id="agent-1", occurred_at="now", reason="accept",
        )
    accepted = controller.transition(
        LifecycleState.ACCEPTED,
        actor_role="requester", actor_id="user-1", occurred_at="now", reason="accept artifact",
    )
    assert accepted.state == LifecycleState.ACCEPTED
    with pytest.raises(LifecycleError, match="cannot transition"):
        controller.transition(
            LifecycleState.WORKING,
            actor_role="agent", actor_id="agent-1", occurred_at="later", reason="continue",
            candidate_id="candidate-3",
        )


def test_uncertain_work_requires_maintainer_resume_at_the_same_state():
    controller = _controller()
    controller.transition(
        LifecycleState.SCOPED,
        actor_role="system", actor_id="server", occurred_at="now", reason="scoped",
    )
    controller.transition(
        LifecycleState.WORKING,
        actor_role="agent", actor_id="agent-1", occurred_at="now", reason="working",
        candidate_id="candidate-1",
    )
    controller.transition(
        LifecycleState.NEEDS_ATTENTION,
        actor_role="system", actor_id="server", occurred_at="now", reason="lost connection",
    )
    with pytest.raises(LifecycleError, match="maintainer"):
        controller.transition(
            LifecycleState.WORKING,
            actor_role="agent", actor_id="agent-1", occurred_at="later", reason="resume",
        )
    with pytest.raises(LifecycleError, match="prior state"):
        controller.transition(
            LifecycleState.SCOPED,
            actor_role="maintainer", actor_id="maintainer-1", occurred_at="later", reason="resume",
        )
    resumed = controller.transition(
        LifecycleState.WORKING,
        actor_role="maintainer", actor_id="maintainer-1", occurred_at="later", reason="resume verified",
    )
    assert resumed.state == LifecycleState.WORKING
    assert resumed.candidate_id == "candidate-1"
