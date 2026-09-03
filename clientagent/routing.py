"""Provider-neutral route policy and deterministic coherence validation.

This module deliberately does not discover, select, dispatch, or approve a
route.  It validates server-supplied facts and untrusted route proposals
against one immutable policy projection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum


ROUTE_POLICY_VERSION = "route-policy/v1"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RouteDecision(StrEnum):
    REJECTED = "rejected"
    LEGAL_PENDING_HUMAN_APPROVAL = "legal_pending_human_approval"


class RouteReason(StrEnum):
    NONE = ""
    POLICY_VERSION_UNSUPPORTED = "route_policy_version_unsupported"
    POLICY_FIELD_MISSING = "route_policy_field_missing"
    POLICY_IDENTIFIER_DUPLICATE = "route_policy_identifier_duplicate"
    POLICY_REFERENCE_UNKNOWN = "route_policy_reference_unknown"
    POLICY_RISK_INVALID = "route_policy_risk_invalid"
    POLICY_BOUNDS_CONTRADICTORY = "route_policy_bounds_contradictory"
    POLICY_DIGEST_MISSING = "route_policy_digest_missing"
    POLICY_DIGEST_MISMATCH = "route_policy_digest_mismatch"
    PLAN_UNIT_MISSING = "route_coherence_plan_unit_missing"
    RISK_INVALID = "route_coherence_risk_invalid"
    INDEPENDENCE_RULE_MISSING = "route_coherence_independence_rule_missing"
    ACCESS_SERVICE_MISSING = "route_coherence_access_service_missing"
    ACCESS_SERVICE_MISMATCH = "route_coherence_access_service_mismatch"
    PROFILE_DUPLICATE = "route_coherence_profile_duplicate"
    PROFILE_MALFORMED = "route_coherence_profile_malformed"
    PROFILE_UNKNOWN = "route_coherence_profile_unknown"
    PROFILE_INELIGIBLE = "route_coherence_profile_ineligible"
    PROPOSAL_COUNT_INVALID = "route_coherence_proposal_count_invalid"
    PROPOSAL_PROFILE_MISMATCH = "route_coherence_proposal_profile_mismatch"
    REQUIRED_CAPABILITY_MISSING = "route_coherence_required_capability_missing"
    PERMISSION_ESCALATION = "route_coherence_permission_escalation"
    ESCALATION_NOT_DISTINCT = "route_coherence_escalation_not_distinct"
    VALIDATOR_NOT_INDEPENDENT = "route_coherence_validator_not_independent"
    FORBIDDEN_FALLBACK = "route_coherence_forbidden_fallback"
    NO_ELIGIBLE_ROUTE = "route_coherence_no_eligible_route"


class RoutePolicyError(ValueError):
    """A stable, machine-classified route-policy validation error."""

    def __init__(self, reason: RouteReason, field: str = "") -> None:
        self.reason = reason
        self.field = field
        detail = f" ({field})" if field else ""
        super().__init__(f"route policy validation failed: {reason.value}{detail}")


@dataclass(frozen=True)
class RiskRule:
    rule_id: str
    minimum_risk: RiskLevel | str
    required_capability_classes: tuple[str, ...]


@dataclass(frozen=True)
class PermissionRule:
    rule_id: str
    required_permission_classes: tuple[str, ...] = ()
    forbidden_permission_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class IndependenceRule:
    rule_id: str
    minimum_risk: RiskLevel | str
    required_validator_capabilities: tuple[str, ...] = ()
    distinct_provider_family: bool = False
    distinct_model_family: bool = False


@dataclass(frozen=True)
class PreferenceRule:
    rule_id: str
    risk: RiskLevel | str
    required_capability_classes: tuple[str, ...] = ()
    preferred_capability_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForbiddenFallback:
    rule_id: str
    from_capability_class: str
    to_capability_class: str


@dataclass(frozen=True)
class RoutePolicy:
    version: str
    capability_classes: tuple[str, ...] = ()
    permission_classes: tuple[str, ...] = ()
    risk_rules: tuple[RiskRule, ...] = ()
    permission_rules: tuple[PermissionRule, ...] = ()
    independence_rules: tuple[IndependenceRule, ...] = ()
    preference_rules: tuple[PreferenceRule, ...] = ()
    forbidden_fallbacks: tuple[ForbiddenFallback, ...] = ()


@dataclass(frozen=True)
class RoutePolicyResult:
    decision: RouteDecision
    reason: RouteReason = RouteReason.NONE
    projection: RoutePolicy | None = None
    digest: str = ""
    requires_fresh_human_approval: bool = True
    machine_advanced: bool = False


@dataclass(frozen=True)
class RouteProfile:
    """A host-supplied profile snapshot; availability is not eligibility."""

    profile_digest: str
    provider_family: str
    model_family: str
    execution_context: str
    access_service_digest: str
    capability_classes: tuple[str, ...]
    permission_classes: tuple[str, ...]
    eligible: bool


@dataclass(frozen=True)
class RouteProposal:
    """An untrusted proposal for a role, not a route selection."""

    profile_digest: str
    provider_family: str
    model_family: str
    execution_context: str
    capability_classes: tuple[str, ...]
    permission_classes: tuple[str, ...]


@dataclass(frozen=True)
class RouteCoherenceRequest:
    plan_unit_id: str
    risk: RiskLevel | str
    required_capability_classes: tuple[str, ...]
    approved_permission_classes: tuple[str, ...]
    policy: RoutePolicy
    policy_digest: str
    independence_rule_id: str
    approved_access_service_digest: str
    profiles: tuple[RouteProfile, ...]
    primary_routes: tuple[RouteProposal, ...]
    escalation_routes: tuple[RouteProposal, ...]
    validator_routes: tuple[RouteProposal, ...]


@dataclass(frozen=True)
class RouteCoherenceResult:
    decision: RouteDecision
    reason: RouteReason = RouteReason.NONE
    policy_digest: str = ""
    requires_fresh_human_approval: bool = True
    machine_advanced: bool = False


def _risk(value: RiskLevel | str, field: str) -> RiskLevel:
    try:
        return RiskLevel(value)
    except (TypeError, ValueError) as exc:
        raise RoutePolicyError(RouteReason.POLICY_RISK_INVALID, field) from exc


def _normalized(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or any(
        not isinstance(value, str) or not value.strip() for value in values
    ):
        raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
    if len(values) != len(set(values)):
        raise RoutePolicyError(RouteReason.POLICY_IDENTIFIER_DUPLICATE, field)
    return tuple(sorted(values))


def _references(values: tuple[str, ...], declared: tuple[str, ...], field: str) -> None:
    if not set(values).issubset(declared):
        raise RoutePolicyError(RouteReason.POLICY_REFERENCE_UNKNOWN, field)


def _register_rule(rule_id: str, seen: set[str], field: str) -> None:
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, f"{field}.rule_id")
    if rule_id in seen:
        raise RoutePolicyError(RouteReason.POLICY_IDENTIFIER_DUPLICATE, f"{field}.rule_id")
    seen.add(rule_id)


def normalize_route_policy(policy: RoutePolicy) -> RoutePolicy:
    """Return a canonical policy projection without mutating the input."""

    if not isinstance(policy, RoutePolicy):
        raise TypeError("policy must be a RoutePolicy")
    if policy.version != ROUTE_POLICY_VERSION:
        raise RoutePolicyError(RouteReason.POLICY_VERSION_UNSUPPORTED, "version")
    capabilities = _normalized(policy.capability_classes, "capability_classes")
    permissions = _normalized(policy.permission_classes, "permission_classes")
    seen: set[str] = set()

    risk_rules: list[RiskRule] = []
    for index, rule in enumerate(policy.risk_rules):
        field = f"risk_rules[{index}]"
        if not isinstance(rule, RiskRule):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _register_rule(rule.rule_id, seen, field)
        required = _normalized(rule.required_capability_classes, f"{field}.required_capability_classes")
        if not required:
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, f"{field}.required_capability_classes")
        _references(required, capabilities, f"{field}.required_capability_classes")
        risk_rules.append(replace(rule, minimum_risk=_risk(rule.minimum_risk, field), required_capability_classes=required))

    permission_rules: list[PermissionRule] = []
    for index, rule in enumerate(policy.permission_rules):
        field = f"permission_rules[{index}]"
        if not isinstance(rule, PermissionRule):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _register_rule(rule.rule_id, seen, field)
        required = _normalized(rule.required_permission_classes, f"{field}.required_permission_classes")
        forbidden = _normalized(rule.forbidden_permission_classes, f"{field}.forbidden_permission_classes")
        if not required and not forbidden:
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _references(required, permissions, f"{field}.required_permission_classes")
        _references(forbidden, permissions, f"{field}.forbidden_permission_classes")
        if set(required) & set(forbidden):
            raise RoutePolicyError(RouteReason.POLICY_BOUNDS_CONTRADICTORY, field)
        permission_rules.append(replace(rule, required_permission_classes=required, forbidden_permission_classes=forbidden))

    independence_rules: list[IndependenceRule] = []
    for index, rule in enumerate(policy.independence_rules):
        field = f"independence_rules[{index}]"
        if not isinstance(rule, IndependenceRule):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        if not isinstance(rule.distinct_provider_family, bool) or not isinstance(rule.distinct_model_family, bool):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _register_rule(rule.rule_id, seen, field)
        required = _normalized(rule.required_validator_capabilities, f"{field}.required_validator_capabilities")
        if not required and not rule.distinct_provider_family and not rule.distinct_model_family:
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _references(required, capabilities, f"{field}.required_validator_capabilities")
        independence_rules.append(replace(rule, minimum_risk=_risk(rule.minimum_risk, field), required_validator_capabilities=required))

    preference_rules: list[PreferenceRule] = []
    for index, rule in enumerate(policy.preference_rules):
        field = f"preference_rules[{index}]"
        if not isinstance(rule, PreferenceRule):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _register_rule(rule.rule_id, seen, field)
        required = _normalized(rule.required_capability_classes, f"{field}.required_capability_classes")
        preferred = _normalized(rule.preferred_capability_classes, f"{field}.preferred_capability_classes")
        if not preferred:
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, f"{field}.preferred_capability_classes")
        _references(required + preferred, capabilities, field)
        preference_rules.append(replace(rule, risk=_risk(rule.risk, field), required_capability_classes=required, preferred_capability_classes=preferred))

    fallbacks: list[ForbiddenFallback] = []
    for index, rule in enumerate(policy.forbidden_fallbacks):
        field = f"forbidden_fallbacks[{index}]"
        if not isinstance(rule, ForbiddenFallback):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        _register_rule(rule.rule_id, seen, field)
        values = (rule.from_capability_class, rule.to_capability_class)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise RoutePolicyError(RouteReason.POLICY_FIELD_MISSING, field)
        if values[0] == values[1]:
            raise RoutePolicyError(RouteReason.POLICY_BOUNDS_CONTRADICTORY, field)
        _references(values, capabilities, field)
        fallbacks.append(rule)

    return RoutePolicy(
        version=policy.version,
        capability_classes=capabilities,
        permission_classes=permissions,
        risk_rules=tuple(sorted(risk_rules, key=lambda item: item.rule_id)),
        permission_rules=tuple(sorted(permission_rules, key=lambda item: item.rule_id)),
        independence_rules=tuple(sorted(independence_rules, key=lambda item: item.rule_id)),
        preference_rules=tuple(sorted(preference_rules, key=lambda item: item.rule_id)),
        forbidden_fallbacks=tuple(sorted(fallbacks, key=lambda item: item.rule_id)),
    )


def _projection_digest(projection: RoutePolicy) -> str:
    payload = json.dumps(asdict(projection), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def route_policy_digest(policy: RoutePolicy) -> str:
    """Return the digest of the canonical projection, regardless of input order."""

    return _projection_digest(normalize_route_policy(policy))


def evaluate_route_policy(policy: RoutePolicy, *, expected_digest: str = "") -> RoutePolicyResult:
    try:
        projection = normalize_route_policy(policy)
    except RoutePolicyError as exc:
        return RoutePolicyResult(RouteDecision.REJECTED, exc.reason)
    digest = _projection_digest(projection)
    if expected_digest and expected_digest != digest:
        return RoutePolicyResult(RouteDecision.REJECTED, RouteReason.POLICY_DIGEST_MISMATCH, projection, digest)
    return RoutePolicyResult(RouteDecision.LEGAL_PENDING_HUMAN_APPROVAL, projection=projection, digest=digest)


_RISK_RANK = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}


def _result(reason: RouteReason, digest: str = "") -> RouteCoherenceResult:
    return RouteCoherenceResult(RouteDecision.REJECTED, reason, digest)


def _class_set(values: tuple[str, ...], declared: tuple[str, ...]) -> set[str] | None:
    if not isinstance(values, (tuple, list)) or any(not isinstance(value, str) or not value for value in values):
        return None
    result = set(values)
    if len(result) != len(values) or not result.issubset(declared):
        return None
    return result


def _profile_reason(profile: RouteProfile, policy: RoutePolicy) -> RouteReason:
    if not isinstance(profile, RouteProfile):
        return RouteReason.PROFILE_MALFORMED
    if (
        any(not isinstance(value, str) or not value for value in (
            profile.profile_digest, profile.provider_family, profile.model_family,
            profile.execution_context, profile.access_service_digest,
        ))
        or not isinstance(profile.eligible, bool)
        or _class_set(profile.capability_classes, policy.capability_classes) is None
        or _class_set(profile.permission_classes, policy.permission_classes) is None
    ):
        return RouteReason.PROFILE_MALFORMED
    return RouteReason.NONE


def _validate_proposal(
    proposal: RouteProposal,
    profiles: dict[str, RouteProfile],
    required_capabilities: tuple[str, ...],
    required_permissions: tuple[str, ...],
    approved_permissions: tuple[str, ...],
    access_service_digest: str,
    policy: RoutePolicy,
) -> tuple[RouteProfile | None, RouteReason]:
    if not isinstance(proposal, RouteProposal):
        return None, RouteReason.PROPOSAL_PROFILE_MISMATCH
    profile = profiles.get(proposal.profile_digest)
    if profile is None:
        return None, RouteReason.PROFILE_UNKNOWN
    if not profile.eligible:
        return None, RouteReason.PROFILE_INELIGIBLE
    if (
        proposal.provider_family != profile.provider_family
        or proposal.model_family != profile.model_family
        or proposal.execution_context != profile.execution_context
        or set(proposal.capability_classes) != set(profile.capability_classes)
        or set(proposal.permission_classes) != set(profile.permission_classes)
        or len(proposal.capability_classes) != len(set(proposal.capability_classes))
        or len(proposal.permission_classes) != len(set(proposal.permission_classes))
    ):
        return None, RouteReason.PROPOSAL_PROFILE_MISMATCH
    if profile.access_service_digest != access_service_digest:
        return None, RouteReason.ACCESS_SERVICE_MISMATCH
    for fallback in policy.forbidden_fallbacks:
        if (
            fallback.from_capability_class in required_capabilities
            and fallback.from_capability_class not in proposal.capability_classes
            and fallback.to_capability_class in proposal.capability_classes
        ):
            return None, RouteReason.FORBIDDEN_FALLBACK
    if not set(required_capabilities).issubset(proposal.capability_classes):
        return None, RouteReason.REQUIRED_CAPABILITY_MISSING
    if not set(required_permissions).issubset(proposal.permission_classes):
        return None, RouteReason.PERMISSION_ESCALATION
    if not set(proposal.permission_classes).issubset(approved_permissions):
        return None, RouteReason.PERMISSION_ESCALATION
    return profile, RouteReason.NONE


def _has_eligible_profile(request: RouteCoherenceRequest, required_permissions: tuple[str, ...]) -> bool:
    for profile in request.profiles:
        if (
            profile.eligible
            and profile.access_service_digest == request.approved_access_service_digest
            and set(request.required_capability_classes).issubset(profile.capability_classes)
            and set(required_permissions).issubset(profile.permission_classes)
            and set(profile.permission_classes).issubset(request.approved_permission_classes)
        ):
            return True
    return False


def evaluate_route_coherence(request: RouteCoherenceRequest) -> RouteCoherenceResult:
    """Check a three-role proposal without selecting, dispatching, or approving it."""

    if not isinstance(request, RouteCoherenceRequest):
        raise TypeError("request must be a RouteCoherenceRequest")
    if not request.plan_unit_id:
        return _result(RouteReason.PLAN_UNIT_MISSING)
    try:
        risk = RiskLevel(request.risk)
    except (TypeError, ValueError):
        return _result(RouteReason.RISK_INVALID)
    if not request.policy_digest:
        return _result(RouteReason.POLICY_DIGEST_MISSING)
    policy_result = evaluate_route_policy(request.policy, expected_digest=request.policy_digest)
    if policy_result.decision is RouteDecision.REJECTED:
        return _result(policy_result.reason, policy_result.digest)
    assert policy_result.projection is not None
    policy = policy_result.projection
    digest = policy_result.digest
    if _class_set(request.required_capability_classes, policy.capability_classes) is None:
        return _result(RouteReason.REQUIRED_CAPABILITY_MISSING, digest)
    if not request.approved_permission_classes or _class_set(request.approved_permission_classes, policy.permission_classes) is None:
        return _result(RouteReason.PERMISSION_ESCALATION, digest)
    for rule in policy.risk_rules:
        if (
            _RISK_RANK[risk] >= _RISK_RANK[RiskLevel(rule.minimum_risk)]
            and not set(rule.required_capability_classes).issubset(request.required_capability_classes)
        ):
            return _result(RouteReason.REQUIRED_CAPABILITY_MISSING, digest)
    required_route_permissions = tuple(sorted({
        permission
        for rule in policy.permission_rules
        for permission in rule.required_permission_classes
    }))
    for rule in policy.permission_rules:
        if (
            not set(rule.required_permission_classes).issubset(request.approved_permission_classes)
            or set(rule.forbidden_permission_classes) & set(request.approved_permission_classes)
        ):
            return _result(RouteReason.PERMISSION_ESCALATION, digest)
    if not request.approved_access_service_digest:
        return _result(RouteReason.ACCESS_SERVICE_MISSING, digest)

    named_independence = next(
        (rule for rule in policy.independence_rules if rule.rule_id == request.independence_rule_id), None
    )
    applicable_independence = tuple(
        rule for rule in policy.independence_rules
        if _RISK_RANK[risk] >= _RISK_RANK[RiskLevel(rule.minimum_risk)]
    )
    if named_independence is None or named_independence not in applicable_independence:
        return _result(RouteReason.INDEPENDENCE_RULE_MISSING, digest)
    validator_capabilities = tuple(sorted({
        capability
        for rule in applicable_independence
        for capability in rule.required_validator_capabilities
    }))
    distinct_provider = any(rule.distinct_provider_family for rule in applicable_independence)
    distinct_model = any(rule.distinct_model_family for rule in applicable_independence)

    profiles: dict[str, RouteProfile] = {}
    for profile in request.profiles:
        reason = _profile_reason(profile, policy)
        if reason is not RouteReason.NONE:
            return _result(reason, digest)
        if profile.profile_digest in profiles:
            return _result(RouteReason.PROFILE_DUPLICATE, digest)
        profiles[profile.profile_digest] = profile

    if not all(len(routes) == 1 for routes in (
        request.primary_routes, request.escalation_routes, request.validator_routes
    )):
        reason = (
            RouteReason.PROPOSAL_COUNT_INVALID
            if _has_eligible_profile(request, required_route_permissions)
            else RouteReason.NO_ELIGIBLE_ROUTE
        )
        return _result(reason, digest)

    primary, reason = _validate_proposal(
        request.primary_routes[0], profiles, request.required_capability_classes,
        required_route_permissions, request.approved_permission_classes,
        request.approved_access_service_digest, policy,
    )
    if reason is not RouteReason.NONE:
        return _result(reason, digest)
    escalation, reason = _validate_proposal(
        request.escalation_routes[0], profiles, request.required_capability_classes,
        required_route_permissions, request.approved_permission_classes,
        request.approved_access_service_digest, policy,
    )
    if reason is not RouteReason.NONE:
        return _result(reason, digest)
    assert primary is not None and escalation is not None
    if primary.profile_digest == escalation.profile_digest:
        return _result(RouteReason.ESCALATION_NOT_DISTINCT, digest)

    validator, reason = _validate_proposal(
        request.validator_routes[0], profiles, validator_capabilities,
        required_route_permissions, request.approved_permission_classes,
        request.approved_access_service_digest, policy,
    )
    if reason is not RouteReason.NONE:
        return _result(reason, digest)
    assert validator is not None
    if (
        validator.profile_digest == primary.profile_digest
        or (distinct_provider and validator.provider_family == primary.provider_family)
        or (distinct_model and validator.model_family == primary.model_family)
    ):
        return _result(RouteReason.VALIDATOR_NOT_INDEPENDENT, digest)
    return RouteCoherenceResult(RouteDecision.LEGAL_PENDING_HUMAN_APPROVAL, policy_digest=digest)


__all__ = [
    "ROUTE_POLICY_VERSION", "ForbiddenFallback", "IndependenceRule", "PermissionRule",
    "PreferenceRule", "RiskLevel", "RiskRule", "RouteCoherenceRequest",
    "RouteCoherenceResult", "RouteDecision", "RoutePolicy", "RoutePolicyError",
    "RoutePolicyResult", "RouteProfile", "RouteProposal", "RouteReason",
    "evaluate_route_coherence", "evaluate_route_policy", "normalize_route_policy",
    "route_policy_digest",
]
