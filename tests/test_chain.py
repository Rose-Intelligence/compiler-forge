"""Chain access, configuration and the fail-closed contract.

The chain layer is the one place where "the network said no" has to become an
exception rather than a shrug. These tests pin that: every failure path either
raises or is reported, and none of them returns a plausible-looking default.

No chain connection is needed — the SDK surface is stubbed, which is also the
point: these assert *our* behaviour on top of the SDK, not the SDK's.
"""

from __future__ import annotations

import json

import pytest

from compilerforge.chain.access import (
    ChainAccess,
    ChainError,
    MetagraphSnapshot,
    NeuronRecord,
    _as_float,
    _attr,
    _succeeded,
)
from compilerforge.utils.config import (
    ConfigError,
    NeuronConfig,
    build_parser,
    config,
)

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class _ValidatorLike:
    neuron_type = "ValidatorNeuron"

    @classmethod
    def add_args(cls, parser):
        from compilerforge.utils.config import add_args, add_validator_args

        add_args(cls, parser)
        add_validator_args(cls, parser)


class _MinerLike:
    neuron_type = "MinerNeuron"

    @classmethod
    def add_args(cls, parser):
        from compilerforge.utils.config import add_args, add_miner_args

        add_args(cls, parser)
        add_miner_args(cls, parser)


def test_dotted_arguments_become_nested_namespaces():
    cfg = config(_ValidatorLike, ["--netuid", "42", "--neuron.public_tasks", "7"])
    assert cfg.netuid == 42
    assert cfg.neuron.public_tasks == 7
    assert cfg.neuron.name == "validator"


def test_every_declared_argument_survives_parsing():
    """The v10 SDK silently dropped these. Nothing may drop them again."""
    cfg = config(
        _ValidatorLike,
        [
            "--netuid",
            "3",
            "--corpus.dir",
            "/tmp/corpus",
            "--measurement.tier_b",
            "--measurement.fuzz_seconds",
            "11",
            "--sandbox.container_cli",
            "podman",
            "--audit.dir",
            "/tmp/audit",
            "--specialist.cells",
            "parsing",
            "--wallet.name",
            "val",
            "--subtensor.network",
            "test",
        ],
    )
    assert cfg.corpus.dir == "/tmp/corpus"
    assert cfg.measurement.tier_b is True
    assert cfg.measurement.fuzz_seconds == 11
    assert cfg.sandbox.container_cli == "podman"
    assert cfg.audit.dir == "/tmp/audit"
    assert cfg.specialist.cells == "parsing"
    assert cfg.wallet.name == "val"
    assert cfg.subtensor.network == "test"


def test_miner_arguments_are_parsed():
    cfg = config(_MinerLike, ["--netuid", "1", "--artifact.image", "ghcr.io/x/y"])
    assert cfg.artifact.image == "ghcr.io/x/y"
    assert cfg.neuron.name == "miner"


def test_an_unknown_setting_raises_instead_of_reading_as_none():
    """A typo must fail loudly. Reading None would evaluate zero tasks and call
    it a healthy round."""
    cfg = config(_ValidatorLike, ["--netuid", "1"])
    with pytest.raises(ConfigError, match="no configuration key"):
        _ = cfg.neuron.tasks_per_round


def test_nested_config_reports_the_keys_it_does_have():
    cfg = NeuronConfig({"alpha": 1})
    with pytest.raises(ConfigError, match="alpha"):
        _ = cfg.beta


def test_the_parser_builds_and_exposes_help():
    parser = build_parser(_ValidatorLike)
    text = parser.format_help()
    for flag in ("--netuid", "--neuron.public_tasks", "--measurement.tier_b", "--wallet.name"):
        assert flag in text


def test_config_round_trips_to_a_plain_dict():
    cfg = config(_ValidatorLike, ["--netuid", "9"])
    as_dict = cfg.to_dict()
    assert as_dict["netuid"] == 9
    assert as_dict["neuron"]["name"] == "validator"
    assert json.dumps(as_dict, default=str)


# ---------------------------------------------------------------------------
# chain access — failure is never silent
# ---------------------------------------------------------------------------


class _FakeSubtensor:
    """Minimal stand-in for the SDK surface ChainAccess touches."""

    def __init__(self, *, block=1000, reads=None, execute=None, submit=None):
        self._block = block
        self._reads = reads or {}
        self._execute = execute
        self._submit = submit
        self.executed = []
        self.submitted = []

    @property
    def block(self):
        if isinstance(self._block, Exception):
            raise self._block
        return self._block

    def read(self, name, **params):
        value = self._reads.get(name)
        if isinstance(value, Exception):
            raise value
        return value(**params) if callable(value) else value

    def execute(self, intent, wallet):
        self.executed.append((intent, wallet))
        if isinstance(self._execute, Exception):
            raise self._execute
        return self._execute

    def submit_call(self, call, wallet):
        self.submitted.append((call, wallet))
        if isinstance(self._submit, Exception):
            raise self._submit
        return self._submit


class _Result:
    def __init__(self, success, message=""):
        self.success = success
        self.message = message


def _access(**kwargs) -> ChainAccess:
    access = ChainAccess(netuid=1)
    access._subtensor = _FakeSubtensor(**kwargs)
    return access


def test_a_failed_read_raises_rather_than_returning_empty():
    access = _access(reads={"metagraph": RuntimeError("rpc down")})
    with pytest.raises(ChainError, match="rpc down"):
        access.metagraph()


def test_a_failed_block_read_raises():
    access = _access(block=RuntimeError("no endpoint"))
    with pytest.raises(ChainError, match="current block"):
        access.current_block()


def test_asking_for_an_unproduced_block_is_refused():
    """The ordering guarantee depends on this: the task-selecting hash must come
    from a block that already exists."""
    access = _access(block=100)
    with pytest.raises(ChainError, match="has not been produced"):
        access.block_hash(500)


def test_a_missing_block_hash_raises_rather_than_returning_empty():
    access = _access(block=1000, reads={"block_info": {"hash": ""}})
    with pytest.raises(ChainError, match="no hash"):
        access.block_hash(900)


def test_a_block_hash_is_returned_when_the_block_exists():
    access = _access(block=1000, reads={"block_info": {"hash": "0xabc"}})
    assert access.block_hash(900) == "0xabc"


def test_a_rejected_extrinsic_raises_instead_of_reporting_success():
    """Reporting weights as set when the chain refused them is the worst of both
    outcomes."""
    access = _access(execute=_Result(False, "InvalidTransaction"))
    with pytest.raises(ChainError, match="InvalidTransaction"):
        access.set_weights(object(), uids=[1], weights=[1.0])


def test_an_accepted_extrinsic_returns_the_result():
    access = _access(execute=_Result(True))
    result = access.set_weights(object(), uids=[1, 2], weights=[0.5, 0.5], mechid=1)
    assert result.success
    intent, _wallet = access._subtensor.executed[0]
    assert intent.mechid == 1
    assert intent.netuid == 1


def test_an_unrecognised_result_shape_counts_as_failure():
    """Guessing 'probably fine' would report weights as set when they were not."""
    assert _succeeded(None) is False
    assert _succeeded(object()) is False
    assert _succeeded({"success": True}) is True


def test_an_empty_weight_vector_is_refused():
    access = _access(execute=_Result(True))
    with pytest.raises(ChainError, match="empty weight vector"):
        access.set_weights(object(), uids=[], weights=[])


def test_a_mismatched_weight_vector_is_refused():
    access = _access(execute=_Result(True))
    with pytest.raises(ChainError, match="length mismatch"):
        access.set_weights(object(), uids=[1, 2], weights=[1.0])


def test_an_oversized_commitment_is_refused_before_submission():
    access = _access(submit=_Result(True))
    with pytest.raises(ChainError, match="single-field limit"):
        access.set_commitment(object(), "x" * 600)
    assert not access._subtensor.submitted


def test_a_commitment_is_submitted_as_raw_fields():
    access = _access(submit=_Result(True))
    access.set_commitment(object(), "hello")
    call, _wallet = access._subtensor.submitted[0]
    assert call is not None


def test_a_rejected_commitment_raises():
    access = _access(submit=_Result(False, "SpaceLimitExceeded"))
    with pytest.raises(ChainError, match="SpaceLimitExceeded"):
        access.set_commitment(object(), "hello")


# ---------------------------------------------------------------------------
# metagraph shape
# ---------------------------------------------------------------------------


def _metagraph_payload():
    return {
        "block": 4242,
        "neurons": [
            {"uid": 0, "hotkey": "hk-a", "coldkey": "ck-a", "total_stake": 10.0},
            {"uid": 1, "hotkey": "hk-b", "coldkey": "ck-b", "total_stake": 2.5},
        ],
        "commitments": {
            0: {"hotkey": "hk-a", "data": '{"v":1}'},
            1: {"hotkey": "hk-b", "data": ""},
        },
    }


def test_commitments_arrive_with_the_metagraph():
    """One read at one block, rather than two that could see different states."""
    access = _access(reads={"metagraph": _metagraph_payload()})
    snapshot = access.metagraph()

    assert snapshot.block == 4242
    assert snapshot.hotkeys() == {"hk-a": 0, "hk-b": 1}
    assert snapshot.commitments == {"hk-a": '{"v":1}'}
    assert snapshot.is_registered("hk-a")
    assert not snapshot.is_registered("hk-missing")


def test_a_missing_metagraph_raises():
    access = _access(reads={"metagraph": None})
    with pytest.raises(ChainError, match="no metagraph"):
        access.metagraph()


def test_reads_tolerate_both_dataclass_and_mapping_shapes():
    """The SDK returns dataclasses for some reads and dicts for others."""

    class Boxed:
        def __init__(self):
            self.hash = "0xdead"

    assert _attr(Boxed(), "hash") == "0xdead"
    assert _attr({"hash": "0xbeef"}, "hash") == "0xbeef"
    assert _attr(None, "hash", "fallback") == "fallback"


def test_balance_like_values_coerce_to_float():
    class Balance:
        tao = 12.5

    assert _as_float(Balance()) == 12.5
    assert _as_float(3) == 3.0
    assert _as_float(None) == 0.0
    assert _as_float(object()) == 0.0


def test_uid_lookup_returns_none_for_an_unknown_hotkey():
    snapshot = MetagraphSnapshot(
        netuid=1,
        block=1,
        neurons=(
            NeuronRecord(
                uid=7,
                hotkey="hk",
                coldkey="ck",
                stake=1.0,
                validator_permit=True,
                last_update=0,
                incentive=0.0,
                emission=0.0,
            ),
        ),
        commitments={},
    )
    assert snapshot.uid_of("hk") == 7
    assert snapshot.uid_of("other") is None
