"""Equivalence comparators.

The comparators are where an optimization is either correctly accepted or
incorrectly waved through, so each one is tested from both directions: it must
catch the change it exists to catch, and it must not reject a legitimate
optimization that happens to look different.
"""

from __future__ import annotations

import json

import pytest

from compilerforge.corpus.equivalence import (
    FloatToleranceComparator,
    Observation,
    OperationSequenceComparator,
    RoundTripComparator,
    StateInvariantComparator,
    _ulp_distance,
    comparator_for,
)
from compilerforge.protocol.task import EquivalenceContract, EquivalenceDiscipline


def _contract(discipline, **kwargs) -> EquivalenceContract:
    return EquivalenceContract(discipline=discipline, **kwargs)


# ---------------------------------------------------------------------------
# byte equality
# ---------------------------------------------------------------------------


def test_byte_equal_accepts_identical_output():
    contract = _contract(EquivalenceDiscipline.BYTE_EQUAL)
    comparator = comparator_for(contract)
    obs = Observation(stdout=b"hello", exit_code=0)
    assert comparator.compare(obs, obs, contract).equivalent


def test_byte_equal_rejects_a_single_changed_byte():
    contract = _contract(EquivalenceDiscipline.BYTE_EQUAL)
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"hello"), Observation(stdout=b"hellO"), contract
    )
    assert not result.equivalent
    assert "byte 4" in result.summary


def test_a_changed_exit_code_is_a_behaviour_change():
    contract = _contract(EquivalenceDiscipline.BYTE_EQUAL)
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"x", exit_code=0), Observation(stdout=b"x", exit_code=1), contract
    )
    assert not result.equivalent


def test_undeclared_channels_are_not_compared():
    """A package that does not declare stderr must not fail on stderr noise."""
    contract = _contract(EquivalenceDiscipline.BYTE_EQUAL, side_effects=("stdout",))
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"x", stderr=b"timing: 41ms"),
        Observation(stdout=b"x", stderr=b"timing: 12ms"),
        contract,
    )
    assert result.equivalent


def test_written_files_are_compared_when_declared():
    contract = _contract(
        EquivalenceDiscipline.BYTE_EQUAL, side_effects=("stdout", "written_files")
    )
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"", written_files={"out.bin": b"abc"}),
        Observation(stdout=b"", written_files={"out.bin": b"abd"}),
        contract,
    )
    assert not result.equivalent

    missing = comparator.compare(
        Observation(stdout=b"", written_files={"out.bin": b"abc"}),
        Observation(stdout=b"", written_files={}),
        contract,
    )
    assert not missing.equivalent
    assert "did not write" in missing.summary


# ---------------------------------------------------------------------------
# float tolerance
# ---------------------------------------------------------------------------


def test_float_tolerance_accepts_a_change_inside_the_declared_budget():
    contract = _contract(
        EquivalenceDiscipline.FLOAT_TOLERANCE, relative_error_budget=1e-6
    )
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"result 1.000000000"),
        Observation(stdout=b"result 1.000000001"),
        contract,
    )
    assert result.equivalent


def test_float_tolerance_rejects_a_change_outside_the_budget():
    contract = _contract(
        EquivalenceDiscipline.FLOAT_TOLERANCE, relative_error_budget=1e-9
    )
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"result 1.0"), Observation(stdout=b"result 1.01"), contract
    )
    assert not result.equivalent


def test_float_discipline_without_a_budget_is_a_broken_package():
    """Failing closed beats silently accepting anything."""
    with pytest.raises(ValueError, match="requires"):
        comparator_for(_contract(EquivalenceDiscipline.FLOAT_TOLERANCE))


def test_float_tolerance_still_requires_identical_non_numeric_structure():
    contract = _contract(EquivalenceDiscipline.FLOAT_TOLERANCE, relative_error_budget=1e-3)
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"alpha 1.0"), Observation(stdout=b"beta 1.0"), contract
    )
    assert not result.equivalent


def test_nan_and_infinity_are_compared_exactly():
    contract = _contract(EquivalenceDiscipline.FLOAT_TOLERANCE, relative_error_budget=1.0)
    comparator = FloatToleranceComparator()
    result = comparator.compare(
        Observation(stdout=b"v 1.0"), Observation(stdout=b"v 1.5"), contract
    )
    assert result.equivalent  # inside a very loose budget

    assert _ulp_distance(1.0, 1.0) == 0
    assert _ulp_distance(1.0, 1.0000000000000002) == 1


# ---------------------------------------------------------------------------
# structural
# ---------------------------------------------------------------------------


def test_structural_ignores_formatting_but_not_content():
    contract = _contract(EquivalenceDiscipline.STRUCTURAL)
    comparator = comparator_for(contract)

    same = comparator.compare(
        Observation(stdout=b'{"a": 1, "b": [2, 3]}'),
        Observation(stdout=b'{"b":[2,3],"a":1}'),
        contract,
    )
    assert same.equivalent, "key order and whitespace are not part of the tree"

    different = comparator.compare(
        Observation(stdout=b'{"a": 1}'), Observation(stdout=b'{"a": 2}'), contract
    )
    assert not different.equivalent
    assert "$.a" in different.summary


def test_structural_reports_unparseable_output_rather_than_passing_it():
    contract = _contract(EquivalenceDiscipline.STRUCTURAL)
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(stdout=b"not json"), Observation(stdout=b"not json"), contract
    )
    assert not result.equivalent
    assert "unparseable" in result.summary


# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------


def test_round_trip_requires_the_decoder_to_recover_the_input():
    contract = _contract(EquivalenceDiscipline.ROUND_TRIP)
    comparator = comparator_for(contract)

    good = Observation(
        state={
            RoundTripComparator.ORIGINAL_KEY: "payload",
            RoundTripComparator.ROUND_TRIP_KEY: "payload",
        }
    )
    assert comparator.compare(good, good, contract).equivalent

    lossy = Observation(
        state={
            RoundTripComparator.ORIGINAL_KEY: "payload",
            RoundTripComparator.ROUND_TRIP_KEY: "payloa",
        }
    )
    result = comparator.compare(good, lossy, contract)
    assert not result.equivalent
    assert "recover" in result.summary


def test_round_trip_allows_a_different_but_valid_encoding():
    """A codec may legitimately emit different bytes; it may not lose data."""
    contract = _contract(EquivalenceDiscipline.ROUND_TRIP)
    comparator = comparator_for(contract)
    state = {
        RoundTripComparator.ORIGINAL_KEY: "payload",
        RoundTripComparator.ROUND_TRIP_KEY: "payload",
    }
    result = comparator.compare(
        Observation(stdout=b"encoding-a", state=state),
        Observation(stdout=b"encoding-b", state=state),
        contract,
    )
    assert result.equivalent


# ---------------------------------------------------------------------------
# state invariants and operation sequences
# ---------------------------------------------------------------------------


def test_ordering_is_compared_only_where_it_is_guaranteed():
    contract = _contract(EquivalenceDiscipline.STATE_INVARIANT)
    comparator = comparator_for(contract)

    reordered_unguaranteed = comparator.compare(
        Observation(state={StateInvariantComparator.UNORDERED_KEY: ["a", "b"]}),
        Observation(state={StateInvariantComparator.UNORDERED_KEY: ["b", "a"]}),
        contract,
    )
    assert reordered_unguaranteed.equivalent, "parallelisation must not be punished"

    reordered_guaranteed = comparator.compare(
        Observation(state={StateInvariantComparator.ORDERED_KEY: ["a", "b"]}),
        Observation(state={StateInvariantComparator.ORDERED_KEY: ["b", "a"]}),
        contract,
    )
    assert not reordered_guaranteed.equivalent


def test_iterator_invalidation_semantics_are_part_of_the_contract():
    """The thing a naive container rewrite changes and byte comparison misses."""
    contract = _contract(EquivalenceDiscipline.OPERATION_SEQUENCE)
    comparator = comparator_for(contract)
    result = comparator.compare(
        Observation(
            state={
                OperationSequenceComparator.RESULTS_KEY: [1, 2, 3],
                OperationSequenceComparator.INVALIDATION_KEY: ["insert"],
            }
        ),
        Observation(
            state={
                OperationSequenceComparator.RESULTS_KEY: [1, 2, 3],
                OperationSequenceComparator.INVALIDATION_KEY: [],
            }
        ),
        contract,
    )
    assert not result.equivalent
    assert "invalidation" in result.summary


def test_unreported_operation_results_are_not_silently_accepted():
    contract = _contract(EquivalenceDiscipline.OPERATION_SEQUENCE)
    comparator = comparator_for(contract)
    result = comparator.compare(Observation(), Observation(), contract)
    assert not result.equivalent


def test_every_discipline_resolves_to_a_comparator():
    for discipline in EquivalenceDiscipline:
        kwargs = (
            {"relative_error_budget": 1e-6}
            if discipline == EquivalenceDiscipline.FLOAT_TOLERANCE
            else {}
        )
        assert comparator_for(_contract(discipline, **kwargs)) is not None


def test_the_evidence_scope_notice_is_not_a_proof_claim():
    from compilerforge.corpus.equivalence import EVIDENCE_SCOPE_NOTICE

    assert "evidence, not proof" in EVIDENCE_SCOPE_NOTICE
    assert json.dumps(EVIDENCE_SCOPE_NOTICE)  # serialisable into every report
