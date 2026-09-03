"""Versioned CLI capability-matrix validation and certification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .governance import GovernanceError, GovernancePolicy, require_startable


class CapabilityMatrixError(ValueError):
    """Raised when a CLI enforcement matrix cannot certify a policy."""


class EnforcementKind(StrEnum):
    NATIVE = "native"
    EXTERNAL = "external"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CapabilityMapping:
    """One governance field mapped to a real enforcement control."""

    field_path: str
    enforcement: EnforcementKind | str
    control_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.field_path, str) or not self.field_path.strip():
            raise ValueError("field_path must be a non-empty string")
        try:
            kind = EnforcementKind(self.enforcement)
        except ValueError as exc:
            raise ValueError("enforcement must be native, external, or unsupported") from exc
        object.__setattr__(self, "enforcement", kind)
        if not isinstance(self.control_id, str) or not self.control_id.strip():
            raise ValueError("control_id must be a non-empty string")

    def as_dict(self) -> dict[str, str]:
        return {
            "field_path": self.field_path,
            "enforcement": self.enforcement.value,
            "control_id": self.control_id,
        }


@dataclass(frozen=True)
class CapabilityCertification:
    """Immutable proof that a matrix was checked against one policy."""

    cli_agent: str
    cli_version: str
    adapter_id: str
    adapter_version: str
    policy_mapping_version: str
    policy_hash: str
    matrix_hash: str
    certified_at: str
    certified_by: str

    def as_dict(self) -> dict[str, str]:
        return {
            "cli_agent": self.cli_agent,
            "cli_version": self.cli_version,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "policy_mapping_version": self.policy_mapping_version,
            "policy_hash": self.policy_hash,
            "matrix_hash": self.matrix_hash,
            "certified_at": self.certified_at,
            "certified_by": self.certified_by,
        }


@dataclass(frozen=True)
class CapabilityMatrix:
    """A server-owned mapping for one exact CLI and adapter version."""

    cli_agent: str
    cli_version: str
    adapter_id: str
    adapter_version: str
    policy_mapping_version: str
    mappings: tuple[CapabilityMapping, ...]

    def __post_init__(self) -> None:
        for field in (
            "cli_agent", "cli_version", "adapter_id", "adapter_version",
            "policy_mapping_version",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.mappings, tuple) or not self.mappings:
            raise ValueError("mappings must be a non-empty tuple")
        if not all(isinstance(mapping, CapabilityMapping) for mapping in self.mappings):
            raise TypeError("mappings must contain CapabilityMapping values")
        paths = [mapping.field_path for mapping in self.mappings]
        if len(paths) != len(set(paths)):
            raise ValueError("mappings cannot contain duplicate field paths")

    def mapping_for(self, field_path: str) -> CapabilityMapping:
        for mapping in self.mappings:
            if mapping.field_path == field_path:
                return mapping
        raise CapabilityMatrixError(f"field has no enforcement mapping: {field_path}")

    def certify(
        self,
        policy: GovernancePolicy,
        *,
        required_fields: Iterable[str],
        certified_at: str,
        certified_by: str,
    ) -> CapabilityCertification:
        """Certify all requested fields against one startable policy."""
        try:
            require_startable(policy)
        except GovernanceError as exc:
            raise CapabilityMatrixError("capability certification requires a startable policy") from exc
        for name, value in (("certified_at", certified_at), ("certified_by", certified_by)):
            if not isinstance(value, str) or not value.strip():
                raise CapabilityMatrixError(f"{name} must be a non-empty string")
        adapter = policy.raw["execution_adapter"]
        for field in ("cli_agent", "cli_version", "adapter_id", "adapter_version", "policy_mapping_version"):
            if getattr(self, field) != adapter[field]:
                raise CapabilityMatrixError(f"{field} mismatch")

        policy_fields = _leaf_paths(policy.raw)
        requested = tuple(dict.fromkeys(required_fields))
        if not requested:
            raise CapabilityMatrixError("required_fields must not be empty")
        for field_path in requested:
            if field_path not in policy_fields:
                raise CapabilityMatrixError(f"mapped field is not in the governance policy: {field_path}")
            mapping = self.mapping_for(field_path)
            if mapping.enforcement is EnforcementKind.UNSUPPORTED:
                raise CapabilityMatrixError(f"required field is unsupported: {field_path}")

        payload = json.dumps(
            [mapping.as_dict() for mapping in self.mappings],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return CapabilityCertification(
            cli_agent=self.cli_agent,
            cli_version=self.cli_version,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            policy_mapping_version=self.policy_mapping_version,
            policy_hash=policy.policy_hash,
            matrix_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            certified_at=certified_at,
            certified_by=certified_by,
        )


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if not isinstance(value, Mapping):
        return {prefix}
    paths: set[str] = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        paths.update(_leaf_paths(child, child_prefix))
    return paths


__all__ = [
    "CapabilityCertification", "CapabilityMapping", "CapabilityMatrix",
    "CapabilityMatrixError", "EnforcementKind",
]
