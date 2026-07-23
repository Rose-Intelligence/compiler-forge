"""Measurement: the deterministic tier, the statistics, and the sign gate.

The deterministic tier is the number that becomes a weight, so its parser is
consensus-critical: a validator that reads a Callgrind file differently from its
peers produces a different weight vector for a program nobody disagrees about.
"""

from __future__ import annotations

import pytest

from compilerforge.evaluation.measurement import (
    CallgrindCounters,
    MeasurementError,
    TierARunner,
    check_sign_agreement,
    parse_callgrind_output,
)
from compilerforge.evaluation.statistics import (
    Distribution,
    bootstrap_ratio_lcb,
    geometric_mean_of_one_plus,
    standard_error_of_mean,
)
from compilerforge.protocol.score import TierAResult
from compilerforge.spec import SPEC

# A real Valgrind 3.22 file: `summary:` is a single zero and the bracketed cost
# lives in `totals:`. Reading the wrong line here silently reports no work done.
GATED_OUTPUT = """\
version: 1
creator: callgrind-3.22.0
cmd: ./build/bench
events: Ir Dr Dw I1mr D1mr D1mw ILmr DLmr DLmw
fl=(1) bench.c
fn=(1) main
16 1000 400 200 1 20 10 1 5 5
summary: 0
totals: 13617465 3105549 1534516 178 3638 2832 178 821 2211
"""

# Other Valgrind builds populate `summary:` instead.
SUMMARY_OUTPUT = """\
events: Ir Dr Dw
summary: 5000 1000 500
totals: 5000 1000 500
"""

EMPTY_OUTPUT = """\
events: Ir Dr Dw
summary: 0
totals: 0
"""


def test_the_bracketed_cost_is_read_regardless_of_which_line_carries_it():
    gated = parse_callgrind_output(GATED_OUTPUT)
    assert gated.ir == 13_617_465
    assert gated.dr == 3_105_549

    summarised = parse_callgrind_output(SUMMARY_OUTPUT)
    assert summarised.ir == 5000


def test_a_run_that_never_reached_its_measured_region_is_an_error_not_a_zero():
    """Reporting zero instructions as a measurement would make every candidate
    look infinitely faster than the baseline."""
    with pytest.raises(MeasurementError, match="no instructions"):
        parse_callgrind_output(EMPTY_OUTPUT)


def test_a_truncated_cost_line_is_not_read_as_a_measurement():
    truncated = "events: Ir Dr Dw\nsummary: 42\n"
    with pytest.raises(MeasurementError):
        parse_callgrind_output(truncated)


def test_output_without_an_events_line_is_refused():
    with pytest.raises(MeasurementError, match="events"):
        parse_callgrind_output("totals: 100 200 300\n")


def test_estimated_cycles_charges_cache_misses():
    cheap = CallgrindCounters(ir=1000)
    costly = CallgrindCounters(ir=1000, d1mr=10, dlmr=10)
    assert costly.estimated_cycles > cheap.estimated_cycles
    assert cheap.estimated_cycles == 1000


def test_a_nondeterministic_benchmark_is_rejected_rather_than_averaged():
    """On a simulated CPU, variation across repeats is not noise — it means the
    program read the clock, the PID or the entropy pool, and it would destroy
    cross-validator agreement if it reached the weight vector."""
    runner = TierARunner()
    steady = [CallgrindCounters(ir=1_000_000) for _ in range(3)]
    jittery = [CallgrindCounters(ir=1_000_000 + i * 50_000) for i in range(3)]

    with pytest.raises(MeasurementError, match="not deterministic"):
        runner.compare(jittery, steady)


def test_identical_repeats_produce_no_uncertainty_discount():
    runner = TierARunner()
    baseline = [CallgrindCounters(ir=1_200_000) for _ in range(3)]
    candidate = [CallgrindCounters(ir=1_000_000) for _ in range(3)]

    result = runner.compare(baseline, candidate)
    assert result.deterministic_speedup == pytest.approx(1.2)
    # A deterministic measurement should not be penalised for uncertainty it
    # does not have.
    assert result.speedup_lcb == pytest.approx(1.2)


def test_comparison_requires_both_sides():
    with pytest.raises(MeasurementError, match="both sides"):
        TierARunner().compare([], [CallgrindCounters(ir=1)])


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def test_the_lower_bound_never_exceeds_the_point_estimate():
    baseline = Distribution(tuple(100.0 + i for i in range(20)))
    candidate = Distribution(tuple(80.0 + i for i in range(20)))
    lcb, point, ucb = bootstrap_ratio_lcb(baseline, candidate, label="t")
    assert lcb <= point <= ucb


def test_noisier_measurements_produce_a_lower_bound():
    tight = Distribution(tuple(80.0 + i * 0.01 for i in range(20)))
    loose = Distribution(tuple(80.0 + i * 3.0 for i in range(20)))
    baseline = Distribution(tuple(100.0 for _ in range(20)))

    tight_lcb, _, _ = bootstrap_ratio_lcb(baseline, tight, label="tight")
    loose_lcb, _, _ = bootstrap_ratio_lcb(baseline, loose, label="loose")
    assert loose_lcb < tight_lcb


def test_the_bootstrap_is_reproducible_across_validators():
    """Two validators resampling the same data must reach the same bound."""
    a = Distribution((100.0, 101.0, 99.0, 102.0, 98.0))
    b = Distribution((80.0, 81.0, 79.0, 82.0, 78.0))
    first = bootstrap_ratio_lcb(a, b, label="repro")
    second = bootstrap_ratio_lcb(a, b, label="repro")
    assert first == second


def test_coefficient_of_variation_matches_the_wall_clock_problem():
    noisy = Distribution(tuple(100.0 + (i % 5) * 2.7 for i in range(30)))
    assert noisy.coefficient_of_variation > 0.0


def test_geometric_aggregation_punishes_repeated_zeroes():
    consistent = geometric_mean_of_one_plus([0.5, 0.5, 0.5, 0.5])
    lopsided = geometric_mean_of_one_plus([2.0, 0.0, 0.0, 0.0])
    assert consistent > lopsided


def test_standard_error_falls_as_evidence_accumulates():
    few = standard_error_of_mean([0.5, 0.6])
    many = standard_error_of_mean([0.5, 0.6] * 20)
    assert many < few


def test_a_single_sample_has_undefined_standard_error():
    assert standard_error_of_mean([0.5]) == float("inf")


# ---------------------------------------------------------------------------
# the sign-agreement gate
# ---------------------------------------------------------------------------


def _tier_a(speedup: float) -> TierAResult:
    return TierAResult(
        instructions_baseline=1_000_000,
        instructions_candidate=int(1_000_000 / speedup),
        deterministic_speedup=speedup,
        speedup_lcb=speedup,
    )


def test_wall_clock_noise_alone_cannot_contradict_the_deterministic_tier():
    """Otherwise ordinary 2.7% variation would void healthy rounds."""
    baseline = Distribution(tuple(100.0 + (i % 7) * 3 for i in range(20)))
    candidate = Distribution(tuple(100.0 + (i % 5) * 3 for i in range(20)))

    agreement = check_sign_agreement(_tier_a(1.05), baseline, candidate)
    assert agreement.agreed
    assert not agreement.should_void


def test_a_confident_wall_clock_regression_contradicts_a_deterministic_win():
    """The interesting adversarial case: fewer instructions, slower program."""
    baseline = Distribution(tuple(100.0 + i * 0.01 for i in range(20)))
    candidate = Distribution(tuple(130.0 + i * 0.01 for i in range(20)))

    first = check_sign_agreement(_tier_a(1.20), baseline, candidate, attempt=0)
    assert not first.agreed
    assert first.should_rerun and not first.should_void

    persistent = check_sign_agreement(
        _tier_a(1.20), baseline, candidate, attempt=SPEC.measurement.sign_agreement_reruns
    )
    assert persistent.should_void


def test_tiers_agreeing_on_direction_passes():
    baseline = Distribution(tuple(100.0 + i * 0.01 for i in range(20)))
    candidate = Distribution(tuple(80.0 + i * 0.01 for i in range(20)))
    assert check_sign_agreement(_tier_a(1.25), baseline, candidate).agreed
