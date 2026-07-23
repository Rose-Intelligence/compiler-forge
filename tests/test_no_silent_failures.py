"""Failures must be loud, attributed, and never mistaken for success.

Every test here corresponds to a way this system could produce a wrong number
while looking healthy. They are grouped by who would wrongly be blamed:

* the miner, for a fault on the validator's side
* every miner, for a fault in a task package
* nobody, because the failure was swallowed entirely
"""

from __future__ import annotations

import json

import pytest

from compilerforge.chain.audit import AuditRepository, RoundBundle
from compilerforge.evaluation.measurement import MeasurementError, parse_callgrind_output
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.scoring.aggregate import RoundAggregator

# ---------------------------------------------------------------------------
# a validator-side fault must not be scored against the miner
# ---------------------------------------------------------------------------


def test_a_voided_artifact_is_excluded_from_aggregation():
    """An evaluation this validator could not perform produces no score.

    Scoring it as zero would punish a miner for a crash on this side of the
    fence; including it as a pass would credit work nobody verified.
    """
    aggregator = RoundAggregator()
    aggregator.register_task("t1", "pkg", "parsing", False)

    aggregator.add(
        ScoreArtifact(
            artifact_digest="sha256:" + "a" * 64,
            task_id="t1",
            toolchain_digest="sha256:" + "1" * 64,
            corpus_snapshot="snap",
            voided=True,
            void_reason="evaluation error: boom",
        )
    )

    assert aggregator.aggregate() == {}, "a voided pair must not reach the standings"


def test_a_real_zero_still_counts_against_the_miner():
    """The complement: a candidate that genuinely failed a gate is scored."""
    aggregator = RoundAggregator()
    aggregator.register_task("t1", "pkg", "parsing", False)

    from compilerforge.protocol.score import GateName, GateResult

    aggregator.add(
        ScoreArtifact(
            artifact_digest="sha256:" + "b" * 64,
            task_id="t1",
            toolchain_digest="sha256:" + "1" * 64,
            corpus_snapshot="snap",
            gates=[GateResult(name=GateName.BUILD, passed=False, detail="did not build")],
        )
    )

    aggregates = aggregator.aggregate()
    assert "sha256:" + "b" * 64 in aggregates
    assert aggregates["sha256:" + "b" * 64].gate_pass_rate == 0.0


def test_scores_from_a_different_consensus_regime_are_refused_not_ignored():
    from compilerforge.scoring.aggregate import IncomparableScores

    aggregator = RoundAggregator()
    artifact = ScoreArtifact(
        artifact_digest="sha256:" + "c" * 64,
        task_id="t1",
        toolchain_digest="sha256:" + "1" * 64,
        corpus_snapshot="snap",
    )
    artifact.spec_digest = "sha256:" + "0" * 64

    with pytest.raises(IncomparableScores):
        aggregator.add(artifact)


# ---------------------------------------------------------------------------
# a task-side fault must void the task, not zero everyone
# ---------------------------------------------------------------------------


def test_a_task_with_no_hidden_inputs_is_unpreparable_not_silently_empty():
    """Returning an empty case list would fail the differential gate for every
    miner, converting a corpus problem into 40 undeserved zeroes."""
    from compilerforge.evaluation.selection import SelectedTask
    from compilerforge.validator.round import RoundRunner

    class _Plan:
        pass

    class _Package:
        class package:  # noqa: N801
            input_generator = None
            package_id = "pkg"

        root = None

    class _Task:
        task_id = "sha256:" + "d" * 64
        seed = "0x1"

    selected = SelectedTask(
        task=_Task(), package=_Package(), profile=None, hidden=False
    )
    plan = _Plan()
    plan.tasks = [selected]

    runner = RoundRunner(ctx=None, workdir=None)
    cases, unpreparable = runner._prepare_cases(plan, {})

    assert cases == {}
    assert _Task.task_id in unpreparable
    assert "cannot be differentially tested" in unpreparable[_Task.task_id]


def test_an_empty_sealed_corpus_is_reported_rather_than_used():
    from compilerforge.evaluation.selection import SelectedTask
    from compilerforge.validator.round import RoundRunner

    class _Package:
        class package:  # noqa: N801
            input_generator = "true"
            package_id = "pkg"

        root = None

    class _Task:
        task_id = "sha256:" + "e" * 64
        seed = "0x1"

    plan = type("Plan", (), {})()
    plan.tasks = [SelectedTask(task=_Task(), package=_Package(), profile=None, hidden=False)]

    runner = RoundRunner(ctx=None, workdir=None)
    _cases, unpreparable = runner._prepare_cases(plan, {_Task.task_id: []})
    assert "empty" in unpreparable[_Task.task_id]


def test_sealing_an_empty_corpus_is_refused():
    from compilerforge.chain.sealed import SealError, seal_differential_cases

    with pytest.raises(SealError, match="empty differential corpus"):
        seal_differential_cases([], label="pkg", hours=1)


# ---------------------------------------------------------------------------
# nothing may be swallowed entirely
# ---------------------------------------------------------------------------


def test_a_measurement_that_recorded_nothing_is_an_error_not_a_zero():
    """Zero instructions would make every candidate look infinitely faster."""
    with pytest.raises(MeasurementError, match="no instructions"):
        parse_callgrind_output("events: Ir Dr Dw\nsummary: 0\ntotals: 0\n")


def test_a_truncated_cost_line_is_never_read_as_a_measurement():
    with pytest.raises(MeasurementError):
        parse_callgrind_output("events: Ir Dr Dw\nsummary: 42\n")


def test_an_unparseable_agent_report_carries_its_reason():
    """A miner told only "malformed report" cannot fix anything."""
    from compilerforge.protocol.task import (
        BenchmarkContract,
        BuildContract,
        EquivalenceContract,
        EquivalenceDiscipline,
        RepositoryContract,
        Task,
        TestContract,
    )
    from compilerforge.sandbox.runner import ArtifactError, ArtifactRun, ArtifactRunner

    task = Task(
        task_id="sha256:" + "0" * 64,
        repository=RepositoryContract(
            uri="mounted:///workspace/repo", revision="0" * 40, license="MIT"
        ),
        build=BuildContract(command="true", toolchain_digest="sha256:" + "1" * 64),
        tests=TestContract(public_command="true", inventory_hash="sha256:" + "2" * 64),
        equivalence=EquivalenceContract(discipline=EquivalenceDiscipline.BYTE_EQUAL),
        benchmark=BenchmarkContract(command="true"),
        seed="0x1",
    )

    run = ArtifactRun(
        exit_code=0,
        patch="",
        report=None,
        wall_seconds=1.0,
        report_error="JSONDecodeError: Expecting value: line 1 column 1",
    )

    with pytest.raises(ArtifactError, match="JSONDecodeError"):
        ArtifactRunner(workdir=None).verify_interface(run, task)


def test_a_corrupt_validator_state_file_is_fatal_not_a_silent_reset():
    """Starting 'fresh' would hand the crown to whoever scores highest next
    round, discarding the defender advantage — invisibly."""
    import inspect

    from compilerforge.base.validator import BaseValidatorNeuron

    source = inspect.getsource(BaseValidatorNeuron.load_state)
    assert "raise RuntimeError" in source
    assert "silently reset" in source


def test_a_corrupt_audit_index_is_rebuilt_and_announced(tmp_path):
    repo = AuditRepository(root=tmp_path)
    bundle = RoundBundle(
        round_number=1,
        block_number=10,
        block_hash="0xabc",
        corpus_snapshot="snap",
        toolchain_digest="sha256:" + "1" * 64,
    )
    repo.publish(bundle)
    assert (tmp_path / "index.json").exists()

    (tmp_path / "index.json").write_text("{ this is not json")

    second = RoundBundle(
        round_number=2,
        block_number=20,
        block_hash="0xdef",
        corpus_snapshot="snap",
        toolchain_digest="sha256:" + "1" * 64,
    )
    repo.publish(second)

    # The damaged file is preserved rather than overwritten, and the index is
    # rebuilt from the round directories that are the real record.
    assert (tmp_path / "index.json.corrupt").exists()
    index = json.loads((tmp_path / "index.json").read_text())
    assert {r["round_number"] for r in index["rounds"]} == {1, 2}


def test_the_audit_index_is_written_atomically(tmp_path):
    repo = AuditRepository(root=tmp_path)
    repo.publish(
        RoundBundle(
            round_number=1,
            block_number=1,
            block_hash="0x1",
            corpus_snapshot="snap",
            toolchain_digest="sha256:" + "1" * 64,
        )
    )
    # No temporary file is left behind for the next reader to trip over.
    assert not list(tmp_path.glob("*.tmp"))


def test_hyperparameter_check_distinguishes_unreadable_from_healthy():
    """'We could not check' and 'everything is fine' must never look the same."""
    from compilerforge.chain.access import ChainError
    from compilerforge.chain.hyperparameters import check_live

    class _Unreadable:
        def hyperparameters(self):
            raise ChainError("rpc timeout")

    problems = check_live(_Unreadable())
    assert problems and "could not read" in problems[0]


def test_hyperparameter_check_reports_a_healthy_subnet_as_empty():
    class _Healthy:
        def hyperparameters(self):
            return {
                "tempo": 7200,
                "commit_reveal_weights_enabled": True,
                "max_allowed_uids": 128,
            }

    assert check_live_of(_Healthy()) == []


def check_live_of(chain):
    from compilerforge.chain.hyperparameters import check_live

    return check_live(chain)


def test_hyperparameter_check_flags_a_misconfigured_subnet():
    class _Misconfigured:
        def hyperparameters(self):
            return {
                "tempo": 360,
                "commit_reveal_weights_enabled": False,
                "max_allowed_uids": 256,
            }

    problems = check_live_of(_Misconfigured())
    joined = " ".join(problems)
    assert "tempo" in joined
    assert "commit_reveal" in joined
    assert "max_allowed_uids" in joined


def test_a_missing_hyperparameter_is_not_read_as_zero():
    """Reading an absent tempo as 0 would report a healthy subnet as broken."""
    from compilerforge.chain.hyperparameters import _as_int

    assert _as_int(None) is None
    assert _as_int("not a number") is None
    assert _as_int("7200") == 7200


# ---------------------------------------------------------------------------
# timelock — verified against the live drand beacon
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_sealed_material_cannot_be_opened_early_and_opens_on_time():
    """The claim the audit story rests on, checked against the real beacon.

    Sealing must be openable by nobody — including the sealer — until the round
    arrives, and must open once it does.
    """
    import time

    from compilerforge.chain.sealed import TimelockNotReady, open_envelope, seal

    envelope = seal(b"hidden corpus", label="test", reveal_in="20s")
    assert envelope.reveal_round > 0

    with pytest.raises(TimelockNotReady):
        open_envelope(envelope)

    started = time.time()
    assert open_envelope(envelope, wait=True) == b"hidden corpus"
    assert time.time() - started < 120


@pytest.mark.slow
def test_revealed_material_that_does_not_match_its_digest_is_rejected():
    """Otherwise a publisher could seal one corpus and reveal another."""
    import time

    from compilerforge.chain.sealed import SealedEnvelope, SealError, open_envelope, seal

    honest = seal(b"the committed corpus", label="honest", reveal_in="1s")
    other = seal(b"a different corpus", label="swapped", reveal_in="1s")

    forged = SealedEnvelope(
        label=other.label,
        ciphertext_hex=other.ciphertext_hex,
        reveal_round=other.reveal_round,
        plaintext_digest=honest.plaintext_digest,
    )

    time.sleep(6)
    with pytest.raises(SealError, match="does not match the sealed digest"):
        open_envelope(forged, wait=True)
