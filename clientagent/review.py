"""Review actions and maintainer handoff for immutable artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .artifacts import ReviewArtifact
from .lifecycle import LifecycleController, LifecycleError, LifecycleState


class ReviewError(ValueError):
    """Raised when a review action or maintainer decision is invalid."""


class ReviewAction(StrEnum):
    ACCEPT_FOR_MAINTAINER_REVIEW = "accept_for_maintainer_review"
    REQUEST_REVISION = "request_revision"
    REJECT_OR_ABANDON = "reject_or_abandon"


class MaintainerDecision(StrEnum):
    APPROVE_FOR_NEXT_MAINTAINER_ACTION = "approve_for_next_maintainer_action"
    REQUEST_REVISION = "request_revision"
    REJECT = "reject"


@dataclass(frozen=True)
class ReviewRecord:
    """One review action bound to the exact artifact the reviewer saw."""

    sequence: int
    action: ReviewAction
    artifact_id: str
    artifact_hash: str
    actor_role: str
    actor_id: str
    occurred_at: str
    comment: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "action": self.action.value,
            "artifact_id": self.artifact_id,
            "artifact_hash": self.artifact_hash,
            "actor_role": self.actor_role,
            "actor_id": self.actor_id,
            "occurred_at": self.occurred_at,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class MaintainerHandoff:
    """The accepted artifact and evidence handed to a separate maintainer."""

    request_id: str
    project_id: str
    artifact_id: str
    artifact_hash: str
    candidate_id: str
    verification: dict[str, Any]
    artifact_history: tuple[dict[str, Any], ...]
    review_history: tuple[ReviewRecord, ...]
    governance_identity: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "accepted_artifact_id": self.artifact_id,
            "accepted_artifact_hash": self.artifact_hash,
            "candidate_id": self.candidate_id,
            "verification": dict(self.verification),
            "artifact_history": list(self.artifact_history),
            "review_history": [record.as_dict() for record in self.review_history],
            "governance": dict(self.governance_identity),
            "integration_authorized": False,
            "release_authorized": False,
            "deployment_authorized": False,
        }


@dataclass(frozen=True)
class MaintainerDecisionRecord:
    """A separate maintainer decision, never a release or deployment command."""

    decision: MaintainerDecision
    artifact_id: str
    artifact_hash: str
    actor_role: str
    actor_id: str
    occurred_at: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "artifact_id": self.artifact_id,
            "artifact_hash": self.artifact_hash,
            "actor_role": self.actor_role,
            "actor_id": self.actor_id,
            "occurred_at": self.occurred_at,
            "reason": self.reason,
            "integration_authorized": False,
            "release_authorized": False,
            "deployment_authorized": False,
        }


class ReviewWorkflow:
    """Connect immutable artifacts, lifecycle transitions, review, and handoff."""

    def __init__(self, lifecycle: LifecycleController) -> None:
        if not isinstance(lifecycle, LifecycleController):
            raise TypeError("lifecycle must be a LifecycleController")
        self._lifecycle = lifecycle
        self._artifacts: dict[str, ReviewArtifact] = {}
        self._reviews: list[ReviewRecord] = []
        self._maintainer_decision: MaintainerDecisionRecord | None = None

    @property
    def lifecycle(self):
        return self._lifecycle.snapshot

    @property
    def lifecycle_controller(self) -> LifecycleController:
        """Return the server-owned controller for non-review processing steps."""
        return self._lifecycle

    @property
    def artifacts(self) -> tuple[ReviewArtifact, ...]:
        return tuple(self._artifacts.values())

    @property
    def reviews(self) -> tuple[ReviewRecord, ...]:
        return tuple(self._reviews)

    @property
    def maintainer_decision(self) -> MaintainerDecisionRecord | None:
        return self._maintainer_decision

    def publish_artifact(
        self,
        artifact: ReviewArtifact,
        *,
        actor_role: str,
        actor_id: str,
        occurred_at: str,
        reason: str,
    ) -> None:
        """Attach a new artifact to the lifecycle and make it reviewable."""
        if not isinstance(artifact, ReviewArtifact):
            raise TypeError("artifact must be a ReviewArtifact")
        if artifact.artifact_id in self._artifacts:
            raise ReviewError("artifact_id already exists; artifacts are immutable")
        snapshot = self._lifecycle.snapshot
        if snapshot.state != LifecycleState.ARTIFACT_BUILDING:
            raise ReviewError("an artifact may be published only from artifact_building")
        if artifact.request_id != snapshot.request_id or artifact.project_id != snapshot.project_id:
            raise ReviewError("artifact request or project identity does not match lifecycle")
        expected_revision = snapshot.revision + 1
        if artifact.revision != expected_revision:
            raise ReviewError(f"artifact revision must be {expected_revision}")
        if artifact.candidate_id != snapshot.candidate_id:
            raise ReviewError("artifact candidate does not match lifecycle candidate")
        if expected_revision == 1:
            if artifact.parent_artifact_id is not None:
                raise ReviewError("first artifact cannot have a parent")
        elif artifact.parent_artifact_id != snapshot.artifact_id:
            raise ReviewError("revision must link to the immediately preceding artifact")
        try:
            self._lifecycle.transition(
                LifecycleState.REVIEW_READY,
                actor_role=actor_role,
                actor_id=actor_id,
                occurred_at=occurred_at,
                reason=reason,
                artifact_id=artifact.artifact_id,
            )
        except LifecycleError as exc:
            raise ReviewError(str(exc)) from exc
        self._artifacts[artifact.artifact_id] = artifact

    def review(
        self,
        action: ReviewAction | str,
        *,
        artifact_id: str,
        artifact_hash: str,
        actor_role: str,
        actor_id: str,
        occurred_at: str,
        comment: str,
    ) -> ReviewRecord:
        """Record one explicit action against the currently displayed artifact."""
        try:
            review_action = ReviewAction(action)
        except ValueError as exc:
            raise ReviewError(f"unknown review action: {action!r}") from exc
        self._text(artifact_id, "artifact_id")
        self._text(artifact_hash, "artifact_hash")
        self._text(actor_role, "actor_role")
        self._text(actor_id, "actor_id")
        self._text(occurred_at, "occurred_at")
        self._text(comment, "comment")
        current = self._lifecycle.snapshot
        artifact = self._artifacts.get(artifact_id)
        if current.state != LifecycleState.REVIEW_READY:
            raise ReviewError("review actions require review_ready state")
        if artifact is None or artifact_hash != artifact.content_hash:
            raise ReviewError("review action is bound to an unknown or substituted artifact")
        if artifact_id != current.artifact_id:
            raise ReviewError("review action must target the current artifact version")
        target = {
            ReviewAction.ACCEPT_FOR_MAINTAINER_REVIEW: LifecycleState.ACCEPTED,
            ReviewAction.REQUEST_REVISION: LifecycleState.REVISION_REQUESTED,
            ReviewAction.REJECT_OR_ABANDON: LifecycleState.REJECTED,
        }[review_action]
        try:
            self._lifecycle.transition(
                target,
                actor_role=actor_role,
                actor_id=actor_id,
                occurred_at=occurred_at,
                reason=comment,
            )
        except LifecycleError as exc:
            raise ReviewError(str(exc)) from exc
        record = ReviewRecord(
            sequence=len(self._reviews) + 1,
            action=review_action,
            artifact_id=artifact_id,
            artifact_hash=artifact_hash,
            actor_role=actor_role,
            actor_id=actor_id,
            occurred_at=occurred_at,
            comment=comment,
        )
        self._reviews.append(record)
        return record

    def start_revision(
        self,
        candidate_id: str,
        *,
        actor_role: str,
        actor_id: str,
        occurred_at: str,
        reason: str,
    ) -> None:
        """Resume only the candidate named for a new revision."""
        try:
            self._lifecycle.transition(
                LifecycleState.WORKING,
                actor_role=actor_role,
                actor_id=actor_id,
                occurred_at=occurred_at,
                reason=reason,
                candidate_id=candidate_id,
            )
        except LifecycleError as exc:
            raise ReviewError(str(exc)) from exc

    def handoff(self) -> MaintainerHandoff:
        """Create a maintainer-only handoff after user acceptance."""
        snapshot = self._lifecycle.snapshot
        if snapshot.state != LifecycleState.ACCEPTED or snapshot.artifact_id is None:
            raise ReviewError("maintainer handoff requires accepted review state")
        artifact = self._artifacts[snapshot.artifact_id]
        return MaintainerHandoff(
            request_id=snapshot.request_id,
            project_id=snapshot.project_id,
            artifact_id=artifact.artifact_id,
            artifact_hash=artifact.content_hash,
            candidate_id=artifact.candidate_id,
            verification=artifact.verification.as_dict(),
            artifact_history=tuple(item.as_dict() for item in self._artifacts.values()),
            review_history=tuple(self._reviews),
            governance_identity=dict(snapshot.governance_identity),
        )

    def decide_as_maintainer(
        self,
        decision: MaintainerDecision | str,
        *,
        artifact_id: str,
        artifact_hash: str,
        actor_role: str,
        actor_id: str,
        occurred_at: str,
        reason: str,
    ) -> MaintainerDecisionRecord:
        """Record a separate maintainer decision without performing integration."""
        try:
            maintainer_decision = MaintainerDecision(decision)
        except ValueError as exc:
            raise ReviewError(f"unknown maintainer decision: {decision!r}") from exc
        if actor_role not in {"maintainer", "governance_owner"}:
            raise ReviewError("only a maintainer or governance owner may decide")
        handoff = self.handoff()
        if artifact_id != handoff.artifact_id or artifact_hash != handoff.artifact_hash:
            raise ReviewError("maintainer decision must target the accepted artifact version")
        self._text(actor_id, "actor_id")
        self._text(occurred_at, "occurred_at")
        self._text(reason, "reason")
        if self._maintainer_decision is not None:
            raise ReviewError("maintainer decision already recorded")
        self._maintainer_decision = MaintainerDecisionRecord(
            maintainer_decision, artifact_id, artifact_hash, actor_role, actor_id, occurred_at, reason,
        )
        return self._maintainer_decision

    @staticmethod
    def _text(value: Any, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ReviewError(f"{name} must be a non-empty string")


__all__ = [
    "MaintainerDecision", "MaintainerDecisionRecord", "MaintainerHandoff", "ReviewAction",
    "ReviewError", "ReviewRecord", "ReviewWorkflow",
]
