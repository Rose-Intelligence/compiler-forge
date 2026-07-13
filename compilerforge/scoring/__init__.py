"""Capture, aggregation, dethronement and the emission policy."""

from compilerforge.scoring.aggregate import (
    ArtifactAggregate,
    IncomparableScores,
    RoundAggregator,
    TaskOutcome,
)
from compilerforge.scoring.capture import (
    compute_capture,
    cross_validator_agreement_score,
    score_components,
)
from compilerforge.scoring.dethronement import (
    ChallengeResult,
    Champion,
    ChampionRegistry,
    select_specialist_top_k,
)
from compilerforge.scoring.emissions import (
    EmissionAllocator,
    Mechanism,
    ReliabilityRecord,
    mechanism_split,
)

__all__ = [
    "ArtifactAggregate",
    "ChallengeResult",
    "Champion",
    "ChampionRegistry",
    "EmissionAllocator",
    "IncomparableScores",
    "Mechanism",
    "ReliabilityRecord",
    "RoundAggregator",
    "TaskOutcome",
    "compute_capture",
    "cross_validator_agreement_score",
    "mechanism_split",
    "score_components",
    "select_specialist_top_k",
]
