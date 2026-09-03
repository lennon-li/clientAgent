from dataclasses import replace

import pytest

from clientagent.routing import (
    ROUTE_POLICY_VERSION,
    ForbiddenFallback,
    IndependenceRule,
    PermissionRule,
    RiskLevel,
    RiskRule,
    RouteCoherenceRequest,
    RouteDecision,
    RoutePolicy,
    RoutePolicyError,
    RouteProfile,
    RouteProposal,
    RouteReason,
    evaluate_route_coherence,
    evaluate_route_policy,
    normalize_route_policy,
    route_policy_digest,
)


def _policy(*, fallback=False):
    return RoutePolicy(
        version=ROUTE_POLICY_VERSION,
        capability_classes=("review", "implementation", "scan"),
        permission_classes=("bounded_write", "read_only"),
        risk_rules=(RiskRule("high-risk", RiskLevel.HIGH, ("review",)),),
        permission_rules=(PermissionRule("read", ("read_only",), ("bounded_write",)),),
        independence_rules=(IndependenceRule("independent-high", RiskLevel.HIGH, ("review",), True, True),),
        forbidden_fallbacks=(ForbiddenFallback("no-review-to-scan", "review", "scan"),) if fallback else (),
    )


def _proposal(profile):
    return RouteProposal(
        profile.profile_digest,
        profile.provider_family,
        profile.model_family,
        profile.execution_context,
        profile.capability_classes,
        profile.permission_classes,
    )


def _request(*, policy=None):
    policy = policy or _policy()
    digest = evaluate_route_policy(policy).digest
    primary = RouteProfile("sha256:primary", "provider-a", "model-a", "direct", "sha256:access", ("review",), ("read_only",), True)
    escalation = RouteProfile("sha256:escalation", "provider-b", "model-b", "direct", "sha256:access", ("review",), ("read_only",), True)
    validator = RouteProfile("sha256:validator", "provider-c", "model-c", "direct", "sha256:access", ("review",), ("read_only",), True)
    return RouteCoherenceRequest(
        plan_unit_id="unit-1",
        risk=RiskLevel.HIGH,
        required_capability_classes=("review",),
        approved_permission_classes=("read_only",),
        policy=policy,
        policy_digest=digest,
        independence_rule_id="independent-high",
        approved_access_service_digest="sha256:access",
        profiles=(primary, escalation, validator),
        primary_routes=(_proposal(primary),),
        escalation_routes=(_proposal(escalation),),
        validator_routes=(_proposal(validator),),
    )


def test_route_policy_is_canonical_and_detects_material_drift():
    first = _policy()
    second = replace(
        first,
        capability_classes=tuple(reversed(first.capability_classes)),
        permission_classes=tuple(reversed(first.permission_classes)),
    )
    assert normalize_route_policy(first) == normalize_route_policy(second)
    assert evaluate_route_policy(first).digest == evaluate_route_policy(second).digest
    assert route_policy_digest(first) == route_policy_digest(second)

    changed = replace(first, capability_classes=first.capability_classes + ("planning",))
    assert evaluate_route_policy(first).digest != evaluate_route_policy(changed).digest
    stale = evaluate_route_policy(first, expected_digest="sha256:stale")
    assert stale.decision is RouteDecision.REJECTED
    assert stale.reason is RouteReason.POLICY_DIGEST_MISMATCH


def test_route_policy_rejects_unknown_references_and_contradictory_bounds():
    with pytest.raises(RoutePolicyError) as unknown:
        normalize_route_policy(replace(_policy(), risk_rules=(RiskRule("bad", "high", ("unknown",)),)))
    assert unknown.value.reason is RouteReason.POLICY_REFERENCE_UNKNOWN

    conflicting = PermissionRule("bad", ("read_only",), ("read_only",))
    with pytest.raises(RoutePolicyError) as bounds:
        normalize_route_policy(replace(_policy(), permission_rules=(conflicting,)))
    assert bounds.value.reason is RouteReason.POLICY_BOUNDS_CONTRADICTORY


def test_valid_route_is_only_legal_pending_human_approval():
    result = evaluate_route_coherence(_request())
    assert result.decision is RouteDecision.LEGAL_PENDING_HUMAN_APPROVAL
    assert result.reason is RouteReason.NONE
    assert result.requires_fresh_human_approval is True
    assert result.machine_advanced is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda request: replace(request, policy_digest=""), RouteReason.POLICY_DIGEST_MISSING),
        (lambda request: replace(request, policy_digest="sha256:stale"), RouteReason.POLICY_DIGEST_MISMATCH),
        (lambda request: replace(request, primary_routes=request.primary_routes * 2), RouteReason.PROPOSAL_COUNT_INVALID),
        (lambda request: replace(request, escalation_routes=request.primary_routes), RouteReason.ESCALATION_NOT_DISTINCT),
        (
            lambda request: replace(
                request,
                profiles=request.profiles[:2] + (replace(request.profiles[2], provider_family="provider-a"),),
                validator_routes=(replace(request.validator_routes[0], provider_family="provider-a"),),
            ),
            RouteReason.VALIDATOR_NOT_INDEPENDENT,
        ),
        (
            lambda request: replace(
                request,
                profiles=(replace(request.profiles[0], access_service_digest="sha256:other"),) + request.profiles[1:],
            ),
            RouteReason.ACCESS_SERVICE_MISMATCH,
        ),
    ],
)
def test_route_coherence_rejects_ambiguous_or_substituted_roles(change, reason):
    result = evaluate_route_coherence(change(_request()))
    assert result.decision is RouteDecision.REJECTED
    assert result.reason is reason
    assert result.machine_advanced is False


def test_route_coherence_rejects_permission_widening_and_capability_fallback():
    request = _request()
    broad_profile = replace(request.profiles[0], permission_classes=("bounded_write",))
    broad = replace(
        request,
        profiles=(broad_profile,) + request.profiles[1:],
        primary_routes=(_proposal(broad_profile),),
    )
    assert evaluate_route_coherence(broad).reason is RouteReason.PERMISSION_ESCALATION

    fallback_request = _request(policy=_policy(fallback=True))
    fallback_profile = replace(fallback_request.profiles[0], capability_classes=("scan",))
    fallback_request = replace(
        fallback_request,
        profiles=(fallback_profile,) + fallback_request.profiles[1:],
        primary_routes=(_proposal(fallback_profile),),
    )
    assert evaluate_route_coherence(fallback_request).reason is RouteReason.FORBIDDEN_FALLBACK


def test_policy_rules_cannot_be_understated_by_the_coherence_request():
    request = _request()
    risk_policy = replace(
        request.policy,
        risk_rules=(RiskRule("high-risk", RiskLevel.HIGH, ("implementation",)),),
    )
    risk_request = replace(
        request,
        policy=risk_policy,
        policy_digest=evaluate_route_policy(risk_policy).digest,
    )
    assert evaluate_route_coherence(risk_request).reason is RouteReason.REQUIRED_CAPABILITY_MISSING

    broad_request = replace(request, approved_permission_classes=("read_only", "bounded_write"))
    assert evaluate_route_coherence(broad_request).reason is RouteReason.PERMISSION_ESCALATION

    permissionless_profile = replace(request.profiles[0], permission_classes=())
    permissionless_request = replace(
        request,
        profiles=(permissionless_profile,) + request.profiles[1:],
        primary_routes=(_proposal(permissionless_profile),),
    )
    assert evaluate_route_coherence(permissionless_request).reason is RouteReason.PERMISSION_ESCALATION


def test_no_available_profile_is_not_treated_as_an_eligible_route():
    request = _request()
    unavailable = replace(request.profiles[0], eligible=False)
    result = evaluate_route_coherence(replace(
        request,
        profiles=(unavailable,),
        primary_routes=(),
        escalation_routes=(),
        validator_routes=(),
    ))
    assert result.reason is RouteReason.NO_ELIGIBLE_ROUTE
