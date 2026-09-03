"""Independent candidate verification primitives."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .governance import GovernanceError, GovernancePolicy, require_startable


class VerificationError(ValueError):
    """Raised when a candidate cannot receive trustworthy verification evidence."""


def _content_bytes(content: bytes | bytearray | str) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    raise TypeError("candidate content must be bytes or text")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class CandidateVersion:
    """Immutable candidate content identified by its content hash."""

    candidate_id: str
    content: bytes
    content_hash: str

    @classmethod
    def create(cls, candidate_id: str, content: bytes | bytearray | str) -> "CandidateVersion":
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError("candidate_id must be a non-empty string")
        immutable_content = _content_bytes(content)
        return cls(candidate_id, immutable_content, _sha256(immutable_content))


@dataclass(frozen=True)
class CheckResult:
    """The result of one server-owned verification check."""

    check_id: str
    passed: bool
    mandatory: bool
    diagnostic: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {
            "check_id": self.check_id,
            "passed": self.passed,
            "mandatory": self.mandatory,
        }
        if self.diagnostic is not None:
            result["diagnostic"] = self.diagnostic
        return result


@dataclass(frozen=True)
class VerificationEvidence:
    """Immutable evidence bound to one exact candidate and policy version."""

    candidate_id: str
    candidate_hash: str
    policy_hash: str
    verifier_id: str
    verifier_version: str
    verified_at: str
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        mandatory = [result for result in self.checks if result.mandatory]
        return bool(mandatory) and all(result.passed for result in mandatory)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_hash": self.candidate_hash,
            "policy_hash": self.policy_hash,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verified_at": self.verified_at,
            "passed": self.passed,
            "checks": [result.as_dict() for result in self.checks],
        }


class IndependentVerifier:
    """Run predefined checks that receive candidate bytes, never agent claims."""

    def __init__(
        self,
        verifier_id: str,
        verifier_version: str,
        checks: Mapping[str, Callable[[bytes], bool]],
    ) -> None:
        for name, value in (("verifier_id", verifier_id), ("verifier_version", verifier_version)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(checks, Mapping) or not checks:
            raise ValueError("checks must be a non-empty mapping")
        if not all(isinstance(key, str) and key.strip() and callable(value)
                   for key, value in checks.items()):
            raise ValueError("checks must map non-empty IDs to callables")
        self._verifier_id = verifier_id
        self._verifier_version = verifier_version
        self._checks = dict(checks)

    def verify(
        self,
        candidate: CandidateVersion,
        policy: GovernancePolicy,
        *,
        verified_at: str,
    ) -> VerificationEvidence:
        """Run policy-declared checks and return evidence, including failures."""
        if not isinstance(candidate, CandidateVersion):
            raise TypeError("candidate must be a CandidateVersion")
        if not isinstance(verified_at, str) or not verified_at.strip():
            raise VerificationError("verified_at must be a non-empty string")
        try:
            require_startable(policy)
        except GovernanceError as exc:
            raise VerificationError("verification requires a startable governance policy") from exc
        controls = policy.raw["verification"]
        if controls["independent_from_agent"] is not True:
            raise VerificationError("policy does not require independent verification")
        if controls["agent_self_report_is_evidence"] is not False:
            raise VerificationError("policy incorrectly accepts agent self-report as evidence")
        mandatory_ids = tuple(controls["mandatory_check_ids"])
        optional_ids = tuple(controls["optional_check_ids"])
        if not mandatory_ids:
            raise VerificationError("policy declares no mandatory verification checks")
        missing = [check_id for check_id in mandatory_ids if check_id not in self._checks]
        if missing:
            raise VerificationError(f"mandatory checks are not registered: {missing}")

        results: list[CheckResult] = []
        for check_id in (*mandatory_ids, *(check_id for check_id in optional_ids if check_id in self._checks)):
            mandatory = check_id in mandatory_ids
            try:
                passed = self._checks[check_id](candidate.content)
                if not isinstance(passed, bool):
                    raise TypeError("check must return boolean")
                results.append(CheckResult(check_id, passed, mandatory))
            except Exception as exc:  # a failed check is evidence of failure, not a verifier success
                results.append(CheckResult(check_id, False, mandatory, type(exc).__name__))
        return VerificationEvidence(
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.content_hash,
            policy_hash=policy.policy_hash,
            verifier_id=self._verifier_id,
            verifier_version=self._verifier_version,
            verified_at=verified_at,
            checks=tuple(results),
        )


__all__ = [
    "CandidateVersion", "CheckResult", "IndependentVerifier", "VerificationError",
    "VerificationEvidence",
]
