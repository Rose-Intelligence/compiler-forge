"""The per-task score primitive.

Raw speedup is a poor primitive: unbounded, disproportionately rewarding one lucky
repository, and silent about how much of the available headroom was captured.
GSO's formulation is better and is adopted here.

    capture(a,t) = clamp( (S_lcb - 1) / (S_ref - 1), 0, C_max )

    0.0  no credible improvement
    1.0  matched the human expert commit
    >1.0 beat the human expert (capped, so one task cannot carry an artifact)

Three properties matter. It is **bounded**, so no single repository can carry an
artifact. It is **comparable across tasks** of different difficulty, because it
normalises by achievable headroom rather than absolute time. And it is
**contamination-resistant**: an artifact consistently above 1.0 has demonstrably
generalised beyond any memorised commit.

The lower confidence bound is used rather than the point estimate, so measurement
uncertainty always costs the miner and never the network — the correct direction
for an incentive system to be wrong in.
"""

from __future__ import annotations

from compilerforge.protocol.score import TierAResult, TierBResult
from compilerforge.spec import SPEC


def compute_capture(*, s_lcb: float, s_ref: float) -> float:
    """Fraction of the reference speedup this artifact achieved.

    ``s_lcb`` is the Tier A lower confidence bound; ``s_ref`` is the reference
    optimization's deterministic speedup, measured once at corpus build time.
    """
    if s_ref < SPEC.capture.min_reference_speedup:
        raise ValueError(
            f"reference speedup {s_ref} is below {SPEC.capture.min_reference_speedup}; "
            "the task cannot normalise capture and should not be in the corpus"
        )
    if s_lcb < SPEC.capture.min_improvement_speedup:
        # An honest null result. Not a penalty.
        return 0.0

    raw = (s_lcb - 1.0) / (s_ref - 1.0)
    return max(0.0, min(raw, SPEC.capture.c_max))


def score_components(
    *,
    capture: float,
    tier_a: TierAResult,
    tier_b: TierBResult | None,
    hidden: bool,
    cross_validator_agreement: float | None = None,
) -> float:
    """Combine the weighted components into one per-task score.

    A component whose evidence is missing contributes nothing rather than a
    guessed value, and the remaining components are *not* renormalised: a
    validator without a calibrated host should report a smaller number, not
    silently inflate the components it does have.

    The one exception is ``hidden_generalisation``, which is a property of the
    task rather than of the measurement — it is awarded in full when the task
    came from a held-out family and is zero otherwise.
    """
    w = SPEC.components
    # Normalise capture into [0,1] so no component can exceed its declared weight
    # just because capture is allowed to reach C_max.
    normalised_capture = min(capture / SPEC.capture.c_max, 1.0)

    score = w.deterministic_capture * normalised_capture

    if hidden:
        score += w.hidden_generalisation * normalised_capture

    if tier_b is not None:
        if tier_b.peak_rss_improvement_pct is not None:
            score += w.peak_memory * _saturating(tier_b.peak_rss_improvement_pct, 25.0)
        if tier_b.p95_improvement_pct is not None:
            score += w.tail_latency * _saturating(tier_b.p95_improvement_pct, 25.0)
        if tier_b.compile_time_change_pct is not None:
            # Positive change means a slower build. A patch that leaves build time
            # alone earns the full component; a pathological template explosion
            # earns none of it and cannot go negative into the other components.
            score += w.compile_time * _saturating(-tier_b.compile_time_change_pct, 10.0)

    if cross_validator_agreement is not None:
        score += w.cross_validator_agreement * max(0.0, min(cross_validator_agreement, 1.0))

    return round(score, 6)


def _saturating(value_pct: float, full_credit_at_pct: float) -> float:
    """Map a percentage improvement onto [0, 1], saturating at full credit.

    Saturating rather than linear-unbounded: the difference between a 25% and a
    60% memory reduction is real but should not let one secondary metric dominate
    a score whose primary signal is deterministic cost.
    """
    if value_pct <= 0:
        return 0.0
    return min(value_pct / full_credit_at_pct, 1.0)


def cross_validator_agreement_score(
    speedups: list[float], *, tolerance: float | None = None
) -> float:
    """How reproducible this artifact's gain was across independent validators.

    Rewards artifacts whose gains are genuinely reproducible rather than sensitive
    to one validator's environment. Worth 5% of the score, and computed by
    the aggregator once all validators' artifacts are in, not by any one of them.
    """
    if len(speedups) < 2:
        # A single observation is not agreement. Award nothing rather than
        # awarding full marks for the absence of contradiction.
        return 0.0

    tol = tolerance if tolerance is not None else SPEC.measurement.tier_a_cross_validator_tolerance
    median = sorted(speedups)[len(speedups) // 2]
    if median <= 0:
        return 0.0

    within = sum(1 for s in speedups if abs(s - median) / median <= tol)
    return within / len(speedups)
