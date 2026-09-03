"""Immutable, verification-bound review artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .governance import GovernanceError, GovernancePolicy, require_startable
from .verification import CandidateVersion, VerificationEvidence


class ArtifactError(ValueError):
    """Raised when an artifact cannot be safely constructed for review."""


def _bytes(content: bytes | bytearray | str) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    raise TypeError("artifact content must be bytes or text")


@dataclass(frozen=True)
class ReviewArtifact:
    """A frozen artifact whose content and verification binding cannot change."""

    artifact_id: str
    schema: str
    revision: int
    agent_id: str
    project_id: str
    request_id: str
    candidate_id: str
    candidate_hash: str
    artifact_type: str
    artifact_format: str
    content: bytes
    content_hash: str
    preview: str
    description: str
    created_by: str
    created_at: str
    parent_artifact_id: str | None
    verification: VerificationEvidence
    limitations: tuple[str, ...]
    assumptions: tuple[str, ...]
    unresolved_decisions: tuple[str, ...]
    classification: str
    retention_days: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "schema": self.schema,
            "revision": self.revision,
            "agent_id": self.agent_id,
            "project_id": self.project_id,
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "artifact_type": self.artifact_type,
            "artifact_format": self.artifact_format,
            "content_hash": self.content_hash,
            "preview": self.preview,
            "description": self.description,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "parent_artifact_id": self.parent_artifact_id,
            "verification": self.verification.as_dict(),
            "limitations": list(self.limitations),
            "assumptions": list(self.assumptions),
            "unresolved_decisions": list(self.unresolved_decisions),
            "classification": self.classification,
            "retention_days": self.retention_days,
        }


class ImmutableArtifactBuilder:
    """Build review artifacts only from passed, exact verification evidence."""

    def build(
        self,
        candidate: CandidateVersion,
        evidence: VerificationEvidence,
        policy: GovernancePolicy,
        *,
        artifact_id: str,
        request_id: str,
        artifact_type: str,
        artifact_format: str,
        preview: str,
        description: str,
        created_by: str,
        created_at: str,
        inspection_passed: bool,
        revision: int = 1,
        parent_artifact_id: str | None = None,
        active_content: bool = False,
        sensitive_data: bool = False,
        limitations: tuple[str, ...] = (),
        assumptions: tuple[str, ...] = (),
        unresolved_decisions: tuple[str, ...] = (),
        classification: str = "public",
        retention_days: int = 0,
    ) -> ReviewArtifact:
        try:
            require_startable(policy)
        except GovernanceError as exc:
            raise ArtifactError("artifact construction requires a startable governance policy") from exc
        self._text(artifact_id, "artifact_id")
        self._text(request_id, "request_id")
        self._text(artifact_type, "artifact_type")
        self._text(artifact_format, "artifact_format")
        self._text(preview, "preview")
        self._text(description, "description")
        self._text(created_by, "created_by")
        self._text(created_at, "created_at")
        self._text(classification, "classification")
        if not isinstance(retention_days, int) or retention_days < 0:
            raise ArtifactError("retention_days must be a non-negative integer")
        if not isinstance(revision, int) or revision < 1:
            raise ArtifactError("revision must be a positive integer")
        if revision > 1 and (not parent_artifact_id or not isinstance(parent_artifact_id, str)):
            raise ArtifactError("revisions require a parent_artifact_id")
        if not isinstance(candidate, CandidateVersion):
            raise TypeError("candidate must be a CandidateVersion")
        if not isinstance(evidence, VerificationEvidence) or not evidence.passed:
            raise ArtifactError("only a candidate with passed mandatory verification may produce an artifact")
        if evidence.candidate_id != candidate.candidate_id or evidence.candidate_hash != candidate.content_hash:
            raise ArtifactError("verification evidence is bound to a different candidate")
        if evidence.policy_hash != policy.policy_hash:
            raise ArtifactError("verification evidence is bound to a different policy")
        if inspection_passed is not True:
            raise ArtifactError("content inspection must pass before artifact construction")
        artifact_policy = policy.raw["artifact_policy"]
        if artifact_policy["builder_id"] != created_by:
            raise ArtifactError("artifact builder identity does not match policy")
        if artifact_type not in artifact_policy["allowed_types"]:
            raise ArtifactError("artifact type is not approved")
        if artifact_format not in artifact_policy["allowed_formats"]:
            raise ArtifactError("artifact format is not approved")
        if active_content and artifact_policy["active_content_allowed"] is not True:
            raise ArtifactError("active content is prohibited by policy")
        if sensitive_data and artifact_policy["sensitive_data_allowed"] is not True:
            raise ArtifactError("sensitive data is prohibited by policy")
        content = _bytes(candidate.content)
        maximum = policy.raw["workload_limits"]["max_artifact_bytes"]
        if not isinstance(maximum, int) or maximum <= 0:
            raise ArtifactError("artifact size budget is disabled")
        if len(content) > maximum:
            raise ArtifactError("artifact exceeds the approved size limit")
        return ReviewArtifact(
            artifact_id=artifact_id,
            schema="clientagent-review-artifact/v1",
            revision=revision,
            agent_id=policy.agent_id,
            project_id=policy.raw["metadata"]["project_id"],
            request_id=request_id,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.content_hash,
            artifact_type=artifact_type,
            artifact_format=artifact_format,
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            preview=preview,
            description=description,
            created_by=created_by,
            created_at=created_at,
            parent_artifact_id=parent_artifact_id,
            verification=evidence,
            limitations=tuple(limitations),
            assumptions=tuple(assumptions),
            unresolved_decisions=tuple(unresolved_decisions),
            classification=classification,
            retention_days=retention_days,
        )

    @staticmethod
    def _text(value: Any, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ArtifactError(f"{name} must be a non-empty string")


__all__ = ["ArtifactError", "ImmutableArtifactBuilder", "ReviewArtifact"]
