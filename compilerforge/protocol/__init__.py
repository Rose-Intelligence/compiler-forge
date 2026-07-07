"""Wire contracts between miners, validators and the chain (interface ``cf/1``)."""

from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.protocol.report import AgentReport
from compilerforge.protocol.score import (
    GateName,
    GateResult,
    ReferenceResult,
    ScoreArtifact,
    TierAResult,
    TierBResult,
)
from compilerforge.protocol.task import (
    BenchmarkContract,
    BuildContract,
    EquivalenceContract,
    EquivalenceDiscipline,
    Objective,
    RepositoryContract,
    ResourceContract,
    Task,
    TestContract,
    WorkloadFamily,
)

__all__ = [
    "AgentReport",
    "ArtifactCommitment",
    "BenchmarkContract",
    "BuildContract",
    "EquivalenceContract",
    "EquivalenceDiscipline",
    "GateName",
    "GateResult",
    "Objective",
    "ReferenceResult",
    "RepositoryContract",
    "ResourceContract",
    "ScoreArtifact",
    "Task",
    "TestContract",
    "TierAResult",
    "TierBResult",
    "WorkloadFamily",
]
