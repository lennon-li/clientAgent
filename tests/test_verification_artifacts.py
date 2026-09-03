from pathlib import Path

import pytest
import yaml

from clientagent.artifacts import ArtifactError, ImmutableArtifactBuilder
from clientagent.governance import load_governance
from clientagent.verification import (
    CandidateVersion,
    IndependentVerifier,
    VerificationError,
)


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "specialized-agent-governance.yaml"


def _replace_placeholders(value):
    if isinstance(value, dict):
        return {key: _replace_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_placeholders(item) for item in value]
    return "configured" if value == "REPLACE_ME" else value


def _write_policy(path: Path):
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


def _policy_and_candidate(tmp_path):
    path = tmp_path / "governance.yaml"
    _write_policy(path)
    return load_governance(path), CandidateVersion.create("candidate-1", "verified result")


def _evidence(policy, candidate):
    return IndependentVerifier(
        "verifier-1", "1.0", {"nonempty": lambda content: bool(content.strip())}
    ).verify(candidate, policy, verified_at="2026-09-02T00:00:00Z")


def test_independent_verifier_binds_passed_evidence_to_exact_candidate(tmp_path):
    policy, candidate = _policy_and_candidate(tmp_path)
    evidence = _evidence(policy, candidate)

    assert evidence.passed is True
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.candidate_hash == candidate.content_hash
    assert evidence.policy_hash == policy.policy_hash
    assert evidence.checks[0].check_id == "nonempty"


def test_verifier_records_failed_checks_and_never_accepts_agent_self_report(tmp_path):
    policy, _ = _policy_and_candidate(tmp_path)
    empty = CandidateVersion.create("candidate-empty", b"")
    evidence = IndependentVerifier(
        "verifier-1", "1.0", {"nonempty": lambda content: bool(content.strip())}
    ).verify(empty, policy, verified_at="now")
    assert evidence.passed is False
    assert evidence.checks[0].passed is False

    with pytest.raises(VerificationError, match="not registered"):
        IndependentVerifier("verifier-1", "1.0", {"other": lambda _: True}).verify(
            empty, policy, verified_at="now"
        )


def test_verifier_requires_at_least_one_mandatory_check(tmp_path):
    policy, candidate = _policy_and_candidate(tmp_path)
    data = yaml.safe_load(policy.path.read_text(encoding="utf-8"))
    data["verification"]["mandatory_check_ids"] = []
    policy.path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    policy = load_governance(policy.path)
    # The approval hash is stale after the policy change, so startability must
    # fail before an empty check set could be treated as successful.
    with pytest.raises(VerificationError, match="startable"):
        _evidence(policy, candidate)


def test_artifact_builder_requires_passed_evidence_and_emits_immutable_binding(tmp_path):
    policy, candidate = _policy_and_candidate(tmp_path)
    evidence = _evidence(policy, candidate)
    artifact = ImmutableArtifactBuilder().build(
        candidate,
        evidence,
        policy,
        artifact_id="artifact-1",
        request_id="request-1",
        artifact_type="report",
        artifact_format="txt",
        preview="Verified result preview",
        description="A verified result.",
        created_by="artifact-builder",
        created_at="2026-09-02T00:00:00Z",
        inspection_passed=True,
    )

    assert artifact.content == b"verified result"
    assert artifact.candidate_hash == candidate.content_hash
    assert artifact.content_hash == candidate.content_hash
    assert artifact.verification.passed is True
    assert artifact.as_dict()["content_hash"] == artifact.content_hash
    with pytest.raises(AttributeError):
        artifact.artifact_id = "changed"


def test_artifact_builder_rejects_unverified_or_mismatched_content(tmp_path):
    policy, candidate = _policy_and_candidate(tmp_path)
    failed = IndependentVerifier(
        "verifier-1", "1.0", {"nonempty": lambda _: False}
    ).verify(candidate, policy, verified_at="now")
    kwargs = dict(
        artifact_id="artifact-1", request_id="request-1", artifact_type="report",
        artifact_format="txt", description="Result", created_by="artifact-builder",
        created_at="now", inspection_passed=True, preview="Result preview",
    )
    with pytest.raises(ArtifactError, match="passed mandatory"):
        ImmutableArtifactBuilder().build(candidate, failed, policy, **kwargs)

    other = CandidateVersion.create("candidate-2", "different")
    with pytest.raises(ArtifactError, match="different candidate"):
        ImmutableArtifactBuilder().build(other, _evidence(policy, candidate), policy, **kwargs)


def test_artifact_builder_enforces_inspection_policy_type_and_revision_limits(tmp_path):
    policy, candidate = _policy_and_candidate(tmp_path)
    evidence = _evidence(policy, candidate)
    builder = ImmutableArtifactBuilder()
    kwargs = dict(
        artifact_id="artifact-1", request_id="request-1", artifact_type="report",
        artifact_format="txt", description="Result", created_by="artifact-builder",
        created_at="now", inspection_passed=False, preview="Result preview",
    )
    with pytest.raises(ArtifactError, match="inspection"):
        builder.build(candidate, evidence, policy, **kwargs)
    with pytest.raises(ArtifactError, match="not approved"):
        builder.build(candidate, evidence, policy, **{**kwargs, "inspection_passed": True, "artifact_type": "image"})
    with pytest.raises(ArtifactError, match="parent_artifact_id"):
        builder.build(candidate, evidence, policy, **{**kwargs, "inspection_passed": True, "revision": 2})
