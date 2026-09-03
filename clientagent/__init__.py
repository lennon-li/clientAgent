"""Core runtime primitives for the clientAgent framework."""

from .governance import (
    GovernanceError,
    GovernancePolicy,
    load_governance,
    require_startable,
    validate_draft,
)
from .deployment import DeploymentAdapter, DeploymentConfig, PolicyAttestation
from .setup import (
    SetupDraft,
    SetupError,
    SetupFinding,
    analyze_draft,
    create_draft,
    effective_policy_summary,
)
from .runtime import (
    RuntimeAdapterError,
    RuntimeAttestation,
    RuntimeCapabilitySnapshot,
    RuntimeEnforcementAdapter,
)
from .lifecycle import (
    LifecycleController,
    LifecycleError,
    LifecycleSnapshot,
    LifecycleState,
    LifecycleTransition,
)
from .verification import (
    CandidateVersion,
    CheckResult,
    IndependentVerifier,
    VerificationError,
    VerificationEvidence,
)
from .artifacts import ArtifactError, ImmutableArtifactBuilder, ReviewArtifact

__all__ = [
    "DeploymentAdapter",
    "DeploymentConfig",
    "GovernanceError",
    "GovernancePolicy",
    "PolicyAttestation",
    "RuntimeAdapterError",
    "RuntimeAttestation",
    "RuntimeCapabilitySnapshot",
    "RuntimeEnforcementAdapter",
    "LifecycleController",
    "LifecycleError",
    "LifecycleSnapshot",
    "LifecycleState",
    "LifecycleTransition",
    "ArtifactError",
    "CandidateVersion",
    "CheckResult",
    "ImmutableArtifactBuilder",
    "IndependentVerifier",
    "ReviewArtifact",
    "VerificationError",
    "VerificationEvidence",
    "SetupDraft",
    "SetupError",
    "SetupFinding",
    "analyze_draft",
    "create_draft",
    "effective_policy_summary",
    "load_governance",
    "require_startable",
    "validate_draft",
]
