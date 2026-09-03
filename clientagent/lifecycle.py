"""Explicit, fail-closed lifecycle state transitions for governed work."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Mapping


class LifecycleError(ValueError):
    """Raised when a lifecycle transition is invalid or insufficiently evidenced."""


class LifecycleState(StrEnum):
    SUBMITTED = "submitted"
    SCOPED = "scoped"
    WORKING = "working"
    VERIFICATION = "verification"
    ARTIFACT_BUILDING = "artifact_building"
    REVIEW_READY = "review_ready"
    REVISION_REQUESTED = "revision_requested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_ATTENTION = "needs_attention"


_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.SUBMITTED: frozenset({LifecycleState.SCOPED, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.SCOPED: frozenset({LifecycleState.WORKING, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.WORKING: frozenset({LifecycleState.VERIFICATION, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.VERIFICATION: frozenset({LifecycleState.ARTIFACT_BUILDING, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.ARTIFACT_BUILDING: frozenset({LifecycleState.REVIEW_READY, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.REVIEW_READY: frozenset({
        LifecycleState.REVISION_REQUESTED, LifecycleState.ACCEPTED,
        LifecycleState.REJECTED, LifecycleState.NEEDS_ATTENTION,
    }),
    LifecycleState.REVISION_REQUESTED: frozenset({LifecycleState.WORKING, LifecycleState.NEEDS_ATTENTION}),
    LifecycleState.NEEDS_ATTENTION: frozenset({
        LifecycleState.SUBMITTED, LifecycleState.SCOPED, LifecycleState.WORKING,
        LifecycleState.VERIFICATION, LifecycleState.ARTIFACT_BUILDING,
        LifecycleState.REVIEW_READY,
    }),
    LifecycleState.ACCEPTED: frozenset(),
    LifecycleState.REJECTED: frozenset(),
}

_PROCESSING_STATES = frozenset({
    LifecycleState.SUBMITTED, LifecycleState.SCOPED, LifecycleState.WORKING,
    LifecycleState.VERIFICATION, LifecycleState.ARTIFACT_BUILDING,
    LifecycleState.REVIEW_READY,
})
_REVIEW_ROLES = frozenset({"requester", "reviewer"})
_RECOVERY_ROLES = frozenset({"maintainer", "governance_owner"})


@dataclass(frozen=True)
class LifecycleTransition:
    """One append-only state transition record."""

    sequence: int
    from_state: LifecycleState
    to_state: LifecycleState
    actor_role: str
    actor_id: str
    occurred_at: str
    reason: str
    candidate_id: str | None
    artifact_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "from": self.from_state.value,
            "to": self.to_state.value,
            "actor_role": self.actor_role,
            "actor_id": self.actor_id,
            "occurred_at": self.occurred_at,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
        }


@dataclass(frozen=True)
class LifecycleSnapshot:
    """Current lifecycle state plus immutable transition history."""

    request_id: str
    project_id: str
    state: LifecycleState
    revision: int
    candidate_id: str | None
    artifact_id: str | None
    history: tuple[LifecycleTransition, ...]
    governance_identity: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "state": self.state.value,
            "revision": self.revision,
            "candidate_id": self.candidate_id,
            "artifact_id": self.artifact_id,
            "history": [event.as_dict() for event in self.history],
            "governance": dict(self.governance_identity),
        }


class LifecycleController:
    """Server-owned transition controller for one request and project."""

    def __init__(self, request_id: str, project_id: str, governance_identity: Mapping[str, str]) -> None:
        for name, value in (("request_id", request_id), ("project_id", project_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(governance_identity, Mapping) or not governance_identity:
            raise ValueError("governance_identity must be a non-empty mapping")
        if not all(isinstance(key, str) and isinstance(value, str) and value.strip()
                   for key, value in governance_identity.items()):
            raise ValueError("governance_identity must contain non-empty string fields")
        self._snapshot = LifecycleSnapshot(
            request_id=request_id,
            project_id=project_id,
            state=LifecycleState.SUBMITTED,
            revision=0,
            candidate_id=None,
            artifact_id=None,
            history=(),
            governance_identity=dict(governance_identity),
        )
        self._attention_return_state: LifecycleState | None = None

    @property
    def snapshot(self) -> LifecycleSnapshot:
        return self._snapshot

    def transition(
        self,
        target: LifecycleState | str,
        *,
        actor_role: str,
        actor_id: str,
        occurred_at: str,
        reason: str,
        candidate_id: str | None = None,
        artifact_id: str | None = None,
    ) -> LifecycleSnapshot:
        """Apply one allowed transition, retaining the prior state forever."""
        try:
            target_state = LifecycleState(target)
        except ValueError as exc:
            raise LifecycleError(f"unknown lifecycle state: {target!r}") from exc
        self._validate_text(actor_role, "actor_role")
        self._validate_text(actor_id, "actor_id")
        self._validate_text(occurred_at, "occurred_at")
        self._validate_text(reason, "reason")
        current = self._snapshot.state
        if target_state not in _TRANSITIONS[current]:
            raise LifecycleError(f"cannot transition from {current.value} to {target_state.value}")
        if target_state == LifecycleState.NEEDS_ATTENTION:
            if current not in _PROCESSING_STATES:
                raise LifecycleError("needs_attention is only valid from a processing or review state")
            self._attention_return_state = current
        elif current == LifecycleState.NEEDS_ATTENTION:
            if actor_role not in _RECOVERY_ROLES:
                raise LifecycleError("only a maintainer or governance owner may resume uncertain work")
            if target_state != self._attention_return_state:
                raise LifecycleError("uncertain work must resume at its prior state")
            self._attention_return_state = None

        if target_state in {LifecycleState.REVISION_REQUESTED, LifecycleState.ACCEPTED, LifecycleState.REJECTED}:
            if actor_role not in _REVIEW_ROLES:
                raise LifecycleError("review decisions require a requester or reviewer role")
            if self._snapshot.artifact_id is None:
                raise LifecycleError("review decision requires a review artifact")
        if target_state == LifecycleState.WORKING:
            if candidate_id is None and self._snapshot.candidate_id is None:
                raise LifecycleError("working state requires a candidate_id")
            if current == LifecycleState.REVISION_REQUESTED and (
                candidate_id is None or candidate_id == self._snapshot.candidate_id
            ):
                raise LifecycleError("a revision requires a new candidate_id")
        if target_state in {LifecycleState.VERIFICATION, LifecycleState.ARTIFACT_BUILDING}:
            if self._snapshot.candidate_id is None and candidate_id is None:
                raise LifecycleError(f"{target_state.value} requires a candidate_id")
        if target_state == LifecycleState.REVIEW_READY:
            if self._snapshot.candidate_id is None and candidate_id is None:
                raise LifecycleError("review_ready requires a candidate_id")
            if artifact_id is None and self._snapshot.artifact_id is None:
                raise LifecycleError("review_ready requires an artifact_id")
        if target_state == LifecycleState.REVISION_REQUESTED and not reason.strip():
            raise LifecycleError("revision request requires a reason")

        next_candidate = candidate_id if candidate_id is not None else self._snapshot.candidate_id
        next_artifact = artifact_id if artifact_id is not None else self._snapshot.artifact_id
        next_revision = self._snapshot.revision
        if target_state == LifecycleState.WORKING and current == LifecycleState.REVISION_REQUESTED:
            next_revision += 1
            next_artifact = None
        event = LifecycleTransition(
            sequence=len(self._snapshot.history) + 1,
            from_state=current,
            to_state=target_state,
            actor_role=actor_role,
            actor_id=actor_id,
            occurred_at=occurred_at,
            reason=reason,
            candidate_id=next_candidate,
            artifact_id=next_artifact,
        )
        self._snapshot = replace(
            self._snapshot,
            state=target_state,
            revision=next_revision,
            candidate_id=next_candidate,
            artifact_id=next_artifact,
            history=(*self._snapshot.history, event),
        )
        return self._snapshot

    @staticmethod
    def _validate_text(value: Any, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise LifecycleError(f"{name} must be a non-empty string")


__all__ = ["LifecycleController", "LifecycleError", "LifecycleSnapshot", "LifecycleState", "LifecycleTransition"]
