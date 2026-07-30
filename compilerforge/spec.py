"""Versioned consensus constants.

Every number a validator uses to turn a measurement into a weight lives here, not
in a code comment and not in an operator's environment file. The dominance margin
and tolerance in particular must be published as versioned consensus constants,
and the same argument applies to every other constant that two honest validators
must agree on to produce comparable weight vectors.

Changing anything in ``ConsensusSpec`` is a consensus-breaking event. It requires
a new ``spec_version``, a scheduled activation block, and validators running the
old version must self-exclude rather than emit incompatible weights.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Final

INTERFACE_VERSION: Final[str] = "cf/1"
# The generalist score is normalised over the whole round task set — an artifact
# is scored 0 on every task it did not clear, not only the ones it attempted — so
# coverage is required and no single favourable task can reach (and freeze) the
# dethronement ceiling. Changing scoring is a fork: artifacts scored under a
# different spec version are not comparable.
SPEC_VERSION: Final[int] = 2


@dataclass(frozen=True, slots=True)
class ScoreComponentWeights:
    """How the per-task score is weighted. Must sum to 1.0."""

    deterministic_capture: float = 0.55
    peak_memory: float = 0.15
    tail_latency: float = 0.10
    hidden_generalisation: float = 0.10
    compile_time: float = 0.05
    cross_validator_agreement: float = 0.05

    def total(self) -> float:
        return (
            self.deterministic_capture
            + self.peak_memory
            + self.tail_latency
            + self.hidden_generalisation
            + self.compile_time
            + self.cross_validator_agreement
        )


@dataclass(frozen=True, slots=True)
class CaptureSpec:
    """The per-task score primitive."""

    # capture is clamped to [0, c_max]. 1.0 == matched the human expert commit.
    c_max: float = 2.0
    # Confidence level for the lower confidence bound on the artifact speedup.
    # Measurement uncertainty must cost the miner, never the network.
    lcb_confidence: float = 0.95
    # Bootstrap resamples used to derive the LCB from repeated measurements.
    bootstrap_resamples: int = 2000
    # A task whose reference patch is not itself an improvement cannot normalise
    # anything; such tasks are rejected at corpus build time.
    min_reference_speedup: float = 1.02
    # Below this, an improvement is an honest null result rather than a win.
    # Not a penalty — simply no positive performance reward.
    min_improvement_speedup: float = 1.01


@dataclass(frozen=True, slots=True)
class BaselineStabilitySpec:
    """An unstable baseline voids the task for every miner."""

    # Tier A is a simulated CPU; anything above this is a broken task package,
    # not hardware noise.
    max_tier_a_relative_spread: float = 0.001
    # Tier B on the calibrated host. Above this the host is not calibrated.
    max_tier_b_coefficient_of_variation: float = 0.02
    min_baseline_repeats: int = 5


@dataclass(frozen=True, slots=True)
class MeasurementSpec:
    """The two-tier measurement protocol."""

    # Tier A (consensus-bearing): Callgrind on a simulated CPU.
    tier_a_repeats: int = 3
    # Tolerance band inside which two validators' Tier A results are "the same".
    # Calibrated empirically in Phase 0 across heterogeneous hosts.
    tier_a_cross_validator_tolerance: float = 0.002
    # Tier B (reporting and gating) on the calibrated bare-metal host.
    tier_b_warmup_runs: int = 3
    tier_b_measured_runs: int = 15
    tier_b_randomise_order: bool = True
    # Sign-agreement gate: Tier A says faster, Tier B says slower with a CI that
    # excludes zero -> flag and re-run; persistent divergence voids the task.
    sign_agreement_reruns: int = 1


@dataclass(frozen=True, slots=True)
class DethronementSpec:
    """Champion/challenger with a statistical margin."""

    # Challenger must exceed the champion aggregate by this margin.
    margin: float = 0.04
    # ...and must not be materially worse on any scored cell.
    cell_tolerance: float = 0.02
    # Required gap = clamp(z * SE, floor, ceiling): few samples demand a large
    # gap, many samples relax toward the floor.
    z: float = 1.96
    gap_floor: float = 0.01
    gap_ceiling: float = 0.25
    # The crown never changes on a single round.
    warmup_rounds: int = 3
    # Specialist cells reward top-K rather than a single champion, because
    # multiple deployment targets are simultaneously useful.
    specialist_top_k: int = 5


@dataclass(frozen=True, slots=True)
class EmissionSpec:
    """Floor-and-decay. Never routine burn."""

    # Mechanism 0 / mechanism 1 split, as u16 weights summing to 65535.
    mechanism_split_u16: tuple[int, int] = (39321, 26214)
    # Within mechanism 1: specialist cells vs the open-source bounty lane.
    specialist_share: float = 0.625
    bounty_share: float = 0.375
    # Floor pool: paid to artifacts that passed every correctness gate and
    # returned an honest null result. Keeps b_i near zero.
    floor_pool_share: float = 0.15
    # Champion decay: an unbeaten champion that has not improved in N rounds
    # decays toward the floor distribution rather than being burned.
    champion_stagnation_rounds: int = 14
    champion_decay_per_round: float = 0.05
    champion_decay_min_share: float = 0.35
    # Emergency safety valve only, published with a reason every activation.
    emergency_burn_enabled: bool = False


@dataclass(frozen=True, slots=True)
class BudgetSpec:
    """Bounded, so the competition is search quality per unit budget."""

    artifact_max_uncompressed_bytes: int = 8 * 1024**3
    default_wall_seconds: int = 1800
    default_model_token_budget: int = 150_000
    default_cpu_cores: int = 8
    default_ram_gb: int = 16
    default_disk_gb: int = 32
    default_pids_max: int = 512
    # Patch hygiene ("optimization harms maintainability").
    max_patch_changed_files: int = 25
    max_patch_added_lines: int = 2000


@dataclass(frozen=True, slots=True)
class SafetySpec:
    """Mandatory V1 safety gates."""

    asan_required: bool = True
    ubsan_required: bool = True
    tsan_required: bool = False  # concurrency cells only; too expensive as a universal gate
    fuzz_seconds: int = 300
    fuzz_required: bool = True
    # Rebuilding the patched source at a second optimization level is a cheap
    # detector for UB-exploiting "speedups".
    second_opt_level: str = "-O1"
    cross_check_second_opt_level: bool = True


@dataclass(frozen=True, slots=True)
class ConsensusSpec:
    """The complete set of constants that define one comparable scoring regime."""

    spec_version: int = SPEC_VERSION
    interface_version: str = INTERFACE_VERSION
    hardware_class: str = "x86_64-v1"

    components: ScoreComponentWeights = field(default_factory=ScoreComponentWeights)
    capture: CaptureSpec = field(default_factory=CaptureSpec)
    stability: BaselineStabilitySpec = field(default_factory=BaselineStabilitySpec)
    measurement: MeasurementSpec = field(default_factory=MeasurementSpec)
    dethronement: DethronementSpec = field(default_factory=DethronementSpec)
    emission: EmissionSpec = field(default_factory=EmissionSpec)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    safety: SafetySpec = field(default_factory=SafetySpec)

    def __post_init__(self) -> None:
        total = self.components.total()
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"score component weights must sum to 1.0, got {total}")
        split = self.emission.mechanism_split_u16
        if sum(split) != 65535:
            raise ValueError(f"mechanism split must sum to 65535, got {sum(split)}")
        lane = self.emission.specialist_share + self.emission.bounty_share
        if abs(lane - 1.0) > 1e-9:
            raise ValueError(f"specialist + bounty share must sum to 1.0, got {lane}")
        for name in ("floor_pool_share", "champion_decay_min_share", "champion_decay_per_round"):
            value = getattr(self.emission, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"emission.{name} must be in [0, 1], got {value}")

    def digest(self) -> str:
        """Content hash of the whole regime.

        Validators refuse to compare scores whose spec digests differ. This is the
        cheapest possible way to keep incompatible regimes from mixing.
        """
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


#: The active regime. Import this rather than constructing your own.
SPEC: Final[ConsensusSpec] = ConsensusSpec()
