"""Command-line configuration for CompilerForge neurons.

Bittensor 11 removed ``bt.Config`` and the ``add_args`` helpers that older
subnets relied on, so configuration is handled here rather than borrowed from
the SDK. That is a net improvement for a subnet with this many knobs: the parser
is ordinary argparse, ``--help`` works, and nothing depends on an environment
variable to decide whether the command line gets read at all.

``NeuronConfig`` gives the dotted-attribute access the rest of the code expects
(``config.neuron.public_tasks``) while refusing unknown attributes, so a typo in
a setting name fails immediately instead of silently reading as ``None``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

DEFAULT_WALLET_PATH = "~/.bittensor/wallets"
DEFAULT_LOGGING_DIR = "~/.bittensor/neurons"


class ConfigError(AttributeError):
    """An unknown configuration key. Always a bug, never a runtime condition."""


class NeuronConfig:
    """A nested, attribute-addressable settings namespace.

    Deliberately strict: reading a key that was never defined raises rather than
    returning ``None``. A validator that silently reads ``config.neuron.tasks``
    as ``None`` because the real name is ``public_tasks`` would evaluate zero
    tasks and report a healthy round.
    """

    __slots__ = ("_values",)

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        object.__setattr__(self, "_values", dict(values or {}))

    def __getattr__(self, name: str) -> Any:
        values = object.__getattribute__(self, "_values")
        if name not in values:
            known = ", ".join(sorted(values)) or "<empty>"
            raise ConfigError(f"no configuration key {name!r}; known keys: {known}")
        return values[name]

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_values")[name] = value

    def __contains__(self, name: str) -> bool:
        return name in object.__getattribute__(self, "_values")

    def get(self, name: str, default: Any = None) -> Any:
        return object.__getattribute__(self, "_values").get(name, default)

    def keys(self):
        return object.__getattribute__(self, "_values").keys()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in object.__getattribute__(self, "_values").items():
            out[key] = value.to_dict() if isinstance(value, NeuronConfig) else value
        return out

    def __repr__(self) -> str:
        return f"NeuronConfig({self.to_dict()!r})"


def _set_nested(config: NeuronConfig, dotted_key: str, value: Any) -> None:
    """Place ``a.b.c`` into nested namespaces."""
    parts = dotted_key.split(".")
    cursor = config
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, NeuronConfig):
            existing = NeuronConfig()
            setattr(cursor, part, existing)
        cursor = existing
    setattr(cursor, parts[-1], value)


def check_config(cls, config: NeuronConfig) -> None:
    """Validate the namespace and prepare the neuron's working directory."""
    full_path = (
        Path(config.logging.logging_dir).expanduser()
        / config.wallet.name
        / config.wallet.hotkey
        / f"netuid{config.netuid}"
        / config.neuron.name
    )
    config.neuron.full_path = str(full_path)
    full_path.mkdir(parents=True, exist_ok=True)

    if config.netuid <= 0:
        raise ValueError(
            f"--netuid must be a positive subnet id, got {config.netuid}. "
            "There is no sensible default; a neuron pointed at the wrong subnet "
            "would read the wrong metagraph and set weights nobody asked for."
        )


def add_chain_args(parser: argparse.ArgumentParser) -> None:
    """Wallet and chain arguments.

    Defined here because Bittensor 11 does not expose ``add_args`` helpers on
    ``Wallet`` and ``Subtensor``.
    """
    wallet = parser.add_argument_group("wallet")
    wallet.add_argument("--wallet.name", type=str, default="default", help="Coldkey name.")
    wallet.add_argument("--wallet.hotkey", type=str, default="default", help="Hotkey name.")
    wallet.add_argument(
        "--wallet.path", type=str, default=DEFAULT_WALLET_PATH, help="Wallet directory."
    )

    chain = parser.add_argument_group("subtensor")
    chain.add_argument(
        "--subtensor.network",
        type=str,
        default="finney",
        help="Network name ('finney', 'test', 'local') or a websocket endpoint.",
    )
    chain.add_argument(
        "--subtensor.fallback_endpoints",
        type=str,
        default="",
        help="Comma-separated fallback endpoints used when the primary is unreachable.",
    )

    logs = parser.add_argument_group("logging")
    logs.add_argument(
        "--logging.logging_dir", type=str, default=DEFAULT_LOGGING_DIR, help="Log directory."
    )
    logs.add_argument("--logging.debug", action="store_true", help="Debug-level logging.")
    logs.add_argument("--logging.trace", action="store_true", help="Trace-level logging.")


def add_args(cls, parser: argparse.ArgumentParser) -> None:
    """Arguments shared by every neuron."""
    neuron_type = "miner" if "miner" in cls.__name__.lower() else "validator"

    parser.add_argument("--netuid", type=int, default=0, help="Subnet netuid. Required.")

    neuron = parser.add_argument_group("neuron")
    neuron.add_argument(
        "--neuron.name",
        type=str,
        default=neuron_type,
        help="Name of this neuron, used to build its working directory.",
    )
    neuron.add_argument(
        "--neuron.epoch_length",
        type=int,
        default=7200,
        help="Blocks per round. One CompilerForge round spans a full tempo, so "
        "this is longer than a typical subnet's.",
    )
    neuron.add_argument(
        "--neuron.events_retention_size",
        type=int,
        default=2 * 1024 * 1024 * 1024,
        help="Event log file retention size in bytes.",
    )
    neuron.add_argument(
        "--neuron.dont_save_events",
        action="store_true",
        help="Do not write events to a log file.",
    )

    work = parser.add_argument_group("work layout")
    work.add_argument(
        "--corpus.dir", type=str, default="./corpus", help="Directory containing task packages."
    )
    work.add_argument(
        "--corpus.snapshot",
        type=str,
        default="cf-corpus-dev",
        help="Corpus snapshot identifier. Part of the comparability tuple: two "
        "validators on different snapshots must not compare scores.",
    )
    work.add_argument(
        "--workdir",
        type=str,
        default="~/.compilerforge",
        help="Scratch and cache directory for builds, baselines and evaluations.",
    )


def add_validator_args(cls, parser: argparse.ArgumentParser) -> None:
    """Validator-only arguments."""
    neuron = parser.add_argument_group("validator round")
    neuron.add_argument(
        "--neuron.public_tasks", type=int, default=25, help="Public corpus tasks per round."
    )
    neuron.add_argument(
        "--neuron.hidden_tasks",
        type=int,
        default=3,
        help="Held-out generalisation tasks per round. At least one is required.",
    )
    neuron.add_argument(
        "--neuron.freeze_lead_blocks",
        type=int,
        default=300,
        help="Blocks between freezing the artifact set and drawing the block hash "
        "that selects tasks. Must be large enough that the hash does not exist "
        "when miners commit.",
    )
    neuron.add_argument(
        "--neuron.heartbeat_blocks",
        type=int,
        default=900,
        help="Blocks between weight heartbeats. Long rounds plus the activity "
        "cutoff make this mandatory, not optional.",
    )
    neuron.add_argument(
        "--neuron.full_production",
        action="store_true",
        help="Produce and score every agent-task pair locally instead of taking "
        "only this validator's share of the split. Splitting production across "
        "validators assumes their stake is comparable, so Yuma merges their "
        "partial weight vectors fairly; when one validator holds most of the "
        "stake, its partial vector decides emission and any agent whose pairs "
        "were assigned elsewhere is starved. On a small agent set the compute is "
        "cheap, so a dominant validator should score everyone and publish a "
        "complete vector.",
    )

    measurement = parser.add_argument_group("measurement")
    measurement.add_argument(
        "--measurement.tier_b",
        action="store_true",
        help="Enable wall-clock measurement. Requires a dedicated calibrated "
        "bare-metal host; without one this validator still participates fully in "
        "consensus through the deterministic tier.",
    )
    measurement.add_argument(
        "--measurement.tier_b_affinity",
        type=str,
        default=None,
        help="CPU list to pin measured processes to, e.g. '4-7'.",
    )
    measurement.add_argument(
        "--measurement.fuzz_seconds",
        type=int,
        default=300,
        help="Coverage-guided fuzzing budget per candidate.",
    )

    sandbox = parser.add_argument_group("sandbox")
    sandbox.add_argument(
        "--sandbox.container_cli",
        type=str,
        default="docker",
        help="Container CLI used to run miner artifacts.",
    )
    sandbox.add_argument(
        "--sandbox.inference_proxy_url",
        type=str,
        default=None,
        help="Loopback URL of the metered inference proxy exposed to artifacts.",
    )
    sandbox.add_argument(
        "--sandbox.allow_unhardened_runtime",
        action="store_true",
        help="Permit a shared-kernel container runtime. Development only: a "
        "validator holds a hotkey on the machine that runs untrusted code.",
    )
    sandbox.add_argument(
        "--sandbox.verify_in_sandbox",
        action="store_true",
        help="Run the verification pipeline (apply, build, run, measure the "
        "miner's patch) inside a container instead of on the host. Requires "
        "--sandbox.verify_image. The toolchain digest is pinned to that image so "
        "every validator running it agrees.",
    )
    sandbox.add_argument(
        "--sandbox.verify_image",
        type=str,
        default=None,
        help="Container image that runs the sandboxed verification. Its toolchain "
        "is the canonical one; all validators must run the same image.",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--audit.dir",
        type=str,
        default="./audit",
        help="Where to write the public audit bundle for each round.",
    )
    output.add_argument(
        "--specialist.cells",
        type=str,
        default="compression,parsing,numerical,data_structures",
        help="Comma-separated workload families scored as specialist cells.",
    )


def add_miner_args(cls, parser: argparse.ArgumentParser) -> None:
    """Miner-only arguments."""
    artifact = parser.add_argument_group("artifact")
    artifact.add_argument(
        "--artifact.image",
        type=str,
        default=None,
        help="Container repository holding the optimization agent, without a tag.",
    )
    artifact.add_argument(
        "--artifact.digest",
        type=str,
        default=None,
        help="sha256 digest of the image to commit. Resolved from the registry when omitted.",
    )
    artifact.add_argument(
        "--artifact.version",
        type=str,
        default="0.1.0",
        help="Informational agent version recorded in the commitment.",
    )
    artifact.add_argument(
        "--artifact.cells",
        type=str,
        default="generalist",
        help="Comma-separated cells this artifact competes in.",
    )


def build_parser(cls) -> argparse.ArgumentParser:
    """Assemble the full argument parser for a neuron class."""
    parser = argparse.ArgumentParser(
        description=f"CompilerForge {getattr(cls, 'neuron_type', 'neuron')}",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_chain_args(parser)
    cls.add_args(parser)
    return parser


def config(cls, args: list[str] | None = None) -> NeuronConfig:
    """Parse the command line into a nested configuration namespace."""
    namespace = build_parser(cls).parse_args(args)
    merged = NeuronConfig()
    for key, value in vars(namespace).items():
        _set_nested(merged, key, value)
    return merged
