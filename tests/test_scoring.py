"""Scoring: capture, aggregation, dethronement and the emission policy.

These are the tests that matter most for consensus. Two validators that disagree
about a build failure lose one candidate; two validators that disagree about how
a measurement becomes a weight diverge permanently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from compilerforge.protocol.score import TierAResult, TierBResult
from compilerforge.scoring.aggregate import ArtifactAggregate, TaskOutcome
from compilerforge.scoring.capture import (
    compute_capture,
    cross_validator_agreement_score,
    score_components,
)
from compilerforge.scoring.dethronement import ChampionRegistry, select_specialist_top_k
from compilerforge.scoring.emissions import EmissionAllocator, ReliabilityRecord
from compilerforge.spec import SPEC

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_is_one_when_the_artifact_matches_the_reference():
    assert compute_capture(s_lcb=1.5, s_ref=1.5) == pytest.approx(1.0)


def test_capture_is_zero_below_the_improvement_threshold():
    # An honest null result, not a penalty.
    assert compute_capture(s_lcb=1.001, s_ref=2.0) == 0.0
    assert compute_capture(s_lcb=0.8, s_ref=2.0) == 0.0


def test_capture_is_capped_so_one_task_cannot_carry_an_artifact():
    # 10x on a task whose reference achieved 1.5x would otherwise dominate the
    # geometric mean across every other task.
    assert compute_capture(s_lcb=10.0, s_ref=1.5) == SPEC.capture.c_max


def test_capture_above_one_means_the_artifact_beat_the_expert():
    assert compute_capture(s_lcb=2.0, s_ref=1.5) == pytest.approx(2.0)


def test_capture_normalises_across_tasks_of_different_difficulty():
    """The point of the primitive: equal fractions of headroom score equally."""
    easy = compute_capture(s_lcb=1.10, s_ref=1.20)  # half of a 20% opportunity
    hard = compute_capture(s_lcb=2.00, s_ref=3.00)  # half of a 200% opportunity
    assert easy == pytest.approx(hard)


def test_capture_rejects_a_reference_that_is_not_an_improvement():
    with pytest.raises(ValueError, match="cannot normalise"):
        compute_capture(s_lcb=1.5, s_ref=1.0)


def test_lower_bound_costs_the_miner_not_the_network():
    """Uncertainty must always push the score down, never up."""
    confident = compute_capture(s_lcb=1.30, s_ref=1.40)
    noisy = compute_capture(s_lcb=1.10, s_ref=1.40)  # same point estimate, wider interval
    assert noisy < confident


# ---------------------------------------------------------------------------
# score components
# ---------------------------------------------------------------------------


def _tier_a(speedup: float = 1.2) -> TierAResult:
    return TierAResult(
        instructions_baseline=1_000_000,
        instructions_candidate=int(1_000_000 / speedup),
        deterministic_speedup=speedup,
        speedup_lcb=speedup,
    )


def test_missing_evidence_contributes_nothing_rather_than_a_guess():
    """A validator without a calibrated host reports less, not approximately more."""
    with_tier_b = score_components(
        capture=1.0,
        tier_a=_tier_a(),
        tier_b=TierBResult(
            median_speedup=1.2,
            speedup_lcb_95=1.15,
            p95_improvement_pct=20.0,
            peak_rss_improvement_pct=20.0,
            compile_time_change_pct=0.0,
        ),
        hidden=False,
    )
    without = score_components(capture=1.0, tier_a=_tier_a(), tier_b=None, hidden=False)
    assert without < with_tier_b


def test_deterministic_capture_dominates_the_score():
    score = score_components(capture=SPEC.capture.c_max, tier_a=_tier_a(), tier_b=None, hidden=False)
    assert score == pytest.approx(SPEC.components.deterministic_capture)


def test_hidden_family_earns_the_generalisation_component():
    public = score_components(capture=1.0, tier_a=_tier_a(), tier_b=None, hidden=False)
    hidden = score_components(capture=1.0, tier_a=_tier_a(), tier_b=None, hidden=True)
    assert hidden > public


def test_a_slower_build_forfeits_the_compile_time_component_without_going_negative():
    explosive = score_components(
        capture=0.0,
        tier_a=_tier_a(1.0),
        tier_b=TierBResult(
            median_speedup=1.0, speedup_lcb_95=1.0, compile_time_change_pct=500.0
        ),
        hidden=False,
    )
    assert explosive >= 0.0


def test_agreement_needs_more_than_one_observation():
    # A single measurement is not agreement, and must not earn full marks for the
    # absence of contradiction.
    assert cross_validator_agreement_score([1.2]) == 0.0
    assert cross_validator_agreement_score([1.2, 1.2, 1.2]) == pytest.approx(1.0)


def test_agreement_falls_when_validators_disagree():
    tight = cross_validator_agreement_score([1.200, 1.2001, 1.1999])
    loose = cross_validator_agreement_score([1.20, 1.60, 0.90])
    assert loose < tight


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def _outcome(capture: float, *, family: str = "parsing", hidden: bool = False, ok: bool = True):
    return TaskOutcome(
        task_id=f"task-{capture}-{family}",
        package_id="pkg",
        family=family,
        hidden=hidden,
        capture=capture,
        score=capture * 0.5,
        verifier_count=3,
        agreement=1.0,
        honest_null=capture == 0.0 and ok,
        gates_passed=ok,
    )


def test_one_huge_win_cannot_mask_repeated_failures():
    """The whole reason aggregation happens in log space."""
    consistent = ArtifactAggregate("sha256:a", tuple(_outcome(0.5) for _ in range(4)))
    lopsided = ArtifactAggregate(
        "sha256:b", (_outcome(2.0), _outcome(0.0), _outcome(0.0), _outcome(0.0))
    )
    assert consistent.generalist_score > lopsided.generalist_score


def test_hidden_and_public_captures_are_reported_separately():
    """Public rising while hidden stays flat is the overfitting warning sign."""
    agg = ArtifactAggregate(
        "sha256:a",
        (_outcome(1.5), _outcome(1.5), _outcome(0.0, hidden=True)),
    )
    assert agg.public_capture > 0
    assert agg.hidden_capture == 0.0


def test_cell_scores_expose_single_family_dominance():
    agg = ArtifactAggregate(
        "sha256:a",
        (_outcome(2.0, family="parsing"), _outcome(0.0, family="compression")),
    )
    cells = agg.cell_scores()
    assert cells["parsing"] > cells["compression"]


def test_honest_null_is_tracked_apart_from_failure():
    honest = ArtifactAggregate("sha256:a", (_outcome(0.0, ok=True),))
    broken = ArtifactAggregate("sha256:b", (_outcome(0.0, ok=False),))
    assert honest.honest_null_rate == 1.0
    assert broken.honest_null_rate == 0.0
    assert honest.gate_pass_rate == 1.0
    assert broken.gate_pass_rate == 0.0


# ---------------------------------------------------------------------------
# dethronement
# ---------------------------------------------------------------------------


def _aggregate(digest: str, capture: float, n: int = 6, family: str = "parsing"):
    return ArtifactAggregate(digest, tuple(_outcome(capture, family=family) for _ in range(n)))


def test_first_artifact_takes_the_crown():
    registry = ChampionRegistry()
    aggregates = {"sha256:a": _aggregate("sha256:a", 0.5)}
    result = registry.evaluate(
        aggregates, commitments={"sha256:a": NOW}, hotkeys={"sha256:a": "hk-a"}
    )
    assert result is not None and result.dethroned
    assert registry.champion.artifact_digest == "sha256:a"


def test_a_near_copy_cannot_dethrone_the_champion():
    """The statistical margin exists to make cloning unprofitable."""
    registry = ChampionRegistry()
    commitments = {"sha256:a": NOW, "sha256:b": NOW + timedelta(hours=1)}
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}

    registry.evaluate(
        {"sha256:a": _aggregate("sha256:a", 0.50)}, commitments=commitments, hotkeys=hotkeys
    )

    # A copy scoring fractionally higher, for many rounds.
    for _ in range(10):
        result = registry.evaluate(
            {
                "sha256:a": _aggregate("sha256:a", 0.50),
                "sha256:b": _aggregate("sha256:b", 0.505),
            },
            commitments=commitments,
            hotkeys=hotkeys,
        )
        assert result is None

    assert registry.champion.artifact_digest == "sha256:a"


def test_a_genuinely_better_challenger_dethrones_after_the_warmup():
    registry = ChampionRegistry()
    commitments = {"sha256:a": NOW, "sha256:b": NOW + timedelta(hours=1)}
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}

    registry.evaluate(
        {"sha256:a": _aggregate("sha256:a", 0.30)}, commitments=commitments, hotkeys=hotkeys
    )

    aggregates = {
        "sha256:a": _aggregate("sha256:a", 0.30),
        "sha256:b": _aggregate("sha256:b", 0.90),
    }
    results = [
        registry.evaluate(aggregates, commitments=commitments, hotkeys=hotkeys)
        for _ in range(SPEC.dethronement.warmup_rounds)
    ]

    # Never on a single round.
    assert results[0] is None
    assert registry.champion.artifact_digest == "sha256:b"


def test_no_crown_change_on_a_noisy_round():
    registry = ChampionRegistry()
    commitments = {"sha256:a": NOW, "sha256:b": NOW}
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}
    registry.evaluate(
        {"sha256:a": _aggregate("sha256:a", 0.10)}, commitments=commitments, hotkeys=hotkeys
    )

    aggregates = {
        "sha256:a": _aggregate("sha256:a", 0.10),
        "sha256:b": _aggregate("sha256:b", 2.00),
    }
    for _ in range(10):
        assert (
            registry.evaluate(
                aggregates, commitments=commitments, hotkeys=hotkeys, round_was_noisy=True
            )
            is None
        )
    assert registry.champion.artifact_digest == "sha256:a"


def test_a_challenger_worse_on_any_cell_does_not_dethrone():
    """A better average must not hide a regression in one workload family."""
    registry = ChampionRegistry()
    commitments = {"sha256:a": NOW, "sha256:b": NOW}
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}

    champion = ArtifactAggregate(
        "sha256:a",
        tuple(
            [_outcome(0.5, family="parsing")] * 3 + [_outcome(0.5, family="compression")] * 3
        ),
    )
    registry.evaluate({"sha256:a": champion}, commitments=commitments, hotkeys=hotkeys)

    challenger = ArtifactAggregate(
        "sha256:b",
        tuple(
            [_outcome(1.5, family="parsing")] * 3 + [_outcome(0.0, family="compression")] * 3
        ),
    )
    for _ in range(SPEC.dethronement.warmup_rounds + 2):
        result = registry.evaluate(
            {"sha256:a": champion, "sha256:b": challenger},
            commitments=commitments,
            hotkeys=hotkeys,
        )
        assert result is None
    assert registry.champion.artifact_digest == "sha256:a"


def test_more_evidence_lowers_the_required_gap():
    registry = ChampionRegistry()
    sparse = _aggregate("sha256:x", 0.5, n=2)
    plentiful = ArtifactAggregate(
        "sha256:y", tuple(_outcome(0.5 + i * 0.001) for i in range(40))
    )
    assert registry.required_gap(plentiful) <= registry.required_gap(sparse)


def test_specialist_cells_reward_top_k():
    aggregates = {
        f"sha256:{i}": _aggregate(f"sha256:{i}", 1.0 - i * 0.1, family="compression")
        for i in range(8)
    }
    ranked = select_specialist_top_k(
        aggregates, "compression", commitments={d: NOW for d in aggregates}, k=3
    )
    assert len(ranked) == 3
    assert [score for _, score in ranked] == sorted(
        (score for _, score in ranked), reverse=True
    )


# ---------------------------------------------------------------------------
# emissions
# ---------------------------------------------------------------------------


def test_nobody_improving_still_pays_correct_work():
    """Floor, do not burn. Withholding miner emission cuts the subnet's own inflow."""
    allocator = EmissionAllocator()
    aggregates = {
        "sha256:a": ArtifactAggregate("sha256:a", (_outcome(0.0), _outcome(0.0))),
        "sha256:b": ArtifactAggregate("sha256:b", (_outcome(0.0), _outcome(0.0))),
    }
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}
    allocator.champions.evaluate(
        aggregates, commitments={d: NOW for d in aggregates}, hotkeys=hotkeys
    )

    weights = allocator.allocate_generalist(aggregates, hotkeys=hotkeys)
    assert weights, "an all-null round must still pay for verified correct work"
    assert sum(weights.values()) == pytest.approx(1.0)


def test_weights_always_normalise_to_one():
    allocator = EmissionAllocator()
    aggregates = {"sha256:a": _aggregate("sha256:a", 0.8)}
    hotkeys = {"sha256:a": "hk-a"}
    allocator.champions.evaluate(
        aggregates, commitments={"sha256:a": NOW}, hotkeys=hotkeys
    )
    weights = allocator.allocate_generalist(aggregates, hotkeys=hotkeys)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_repeat_correctness_offenders_leave_the_floor_pool():
    allocator = EmissionAllocator()
    allocator.reliability["sha256:bad"] = ReliabilityRecord(
        artifact_digest="sha256:bad", rounds_participated=10, correctness_failures=5
    )
    aggregates = {
        "sha256:bad": ArtifactAggregate("sha256:bad", (_outcome(0.0),)),
        "sha256:good": ArtifactAggregate("sha256:good", (_outcome(0.0),)),
    }
    hotkeys = {"sha256:bad": "hk-bad", "sha256:good": "hk-good"}
    weights = allocator.allocate_generalist(aggregates, hotkeys=hotkeys)
    assert "hk-bad" not in weights
    assert "hk-good" in weights


def test_a_stagnant_champion_decays_toward_the_floor():
    allocator = EmissionAllocator()
    aggregates = {
        "sha256:a": _aggregate("sha256:a", 0.5),
        "sha256:b": _aggregate("sha256:b", 0.1),
    }
    hotkeys = {"sha256:a": "hk-a", "sha256:b": "hk-b"}
    commitments = {d: NOW for d in aggregates}
    allocator.champions.evaluate(aggregates, commitments=commitments, hotkeys=hotkeys)

    fresh = allocator.allocate_generalist(aggregates, hotkeys=hotkeys)["hk-a"]

    for _ in range(SPEC.emission.champion_stagnation_rounds + 10):
        allocator.champions.evaluate(aggregates, commitments=commitments, hotkeys=hotkeys)

    stale = allocator.allocate_generalist(aggregates, hotkeys=hotkeys)["hk-a"]
    assert stale < fresh
    # Decayed, not burned: the share went to other miners, not to a burn UID.
    assert sum(allocator.allocate_generalist(aggregates, hotkeys=hotkeys).values()) == pytest.approx(1.0)


def test_emergency_burn_is_disabled_by_default():
    allocator = EmissionAllocator()
    with pytest.raises(RuntimeError, match="disabled"):
        allocator.emergency_burn(
            share=0.5, reason="test", owner_hotkey="hk-owner", round_number=1
        )
