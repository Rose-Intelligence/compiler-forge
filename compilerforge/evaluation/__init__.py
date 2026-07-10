"""The seven-stage authoritative evaluation protocol."""

from compilerforge.evaluation.baseline import Baseline, BaselineBuilder, BaselineUnstable
from compilerforge.evaluation.build import Builder, Workspace, toolchain_digest
from compilerforge.evaluation.differential import DifferentialCase, DifferentialRunner
from compilerforge.evaluation.measurement import (
    MeasurementError,
    TierARunner,
    TierBRunner,
    check_sign_agreement,
)
from compilerforge.evaluation.pipeline import (
    CandidatePatch,
    EvaluationContext,
    Evaluator,
    TaskVoided,
)
from compilerforge.evaluation.selection import (
    RoundPlan,
    RoundSeed,
    SelectedTask,
    SelectionError,
    derive_round,
)

__all__ = [
    "Baseline",
    "BaselineBuilder",
    "BaselineUnstable",
    "Builder",
    "CandidatePatch",
    "DifferentialCase",
    "DifferentialRunner",
    "EvaluationContext",
    "Evaluator",
    "MeasurementError",
    "RoundPlan",
    "RoundSeed",
    "SelectedTask",
    "SelectionError",
    "TaskVoided",
    "TierARunner",
    "TierBRunner",
    "Workspace",
    "check_sign_agreement",
    "derive_round",
    "toolchain_digest",
]
