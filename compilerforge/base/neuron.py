"""Base class shared by the CompilerForge miner and validator.

Handles the parts every neuron needs and no more: wallet and chain objects,
registration checking, metagraph resynchronisation on an epoch boundary, and the
hook points a subclass fills in.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from pathlib import Path

import bittensor as bt

from compilerforge import __spec_version__
from compilerforge.chain.access import ChainAccess, ChainError, MetagraphSnapshot
from compilerforge.utils.config import NeuronConfig, add_args, check_config, config
from compilerforge.utils.logging import configure, logger, setup_events_logger


class NeuronNotRegistered(RuntimeError):
    """The hotkey is not on the subnet. Registering is the operator's job."""


class BaseNeuron(ABC):
    """Common behaviour for both neuron types."""

    neuron_type: str = "BaseNeuron"
    spec_version: int = __spec_version__

    @classmethod
    def check_config(cls, config: NeuronConfig) -> None:
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser) -> None:
        add_args(cls, parser)

    @classmethod
    def config(cls) -> NeuronConfig:
        return config(cls)

    @property
    def block(self) -> int:
        return self.chain.current_block()

    def __init__(self, config: NeuronConfig | None = None) -> None:
        # One parse, not two. The subclass parser is a superset of the base one,
        # so re-parsing and merging would only risk the two disagreeing.
        self.config = copy.deepcopy(config) if config is not None else type(self).config()
        self.check_config(self.config)

        configure(debug=self.config.logging.debug, trace=self.config.logging.trace)
        if not self.config.neuron.dont_save_events:
            setup_events_logger(
                self.config.neuron.full_path, self.config.neuron.events_retention_size
            )

        self.wallet = bt.Wallet(
            name=self.config.wallet.name,
            hotkey=self.config.wallet.hotkey,
            path=str(Path(self.config.wallet.path).expanduser()),
        )
        fallbacks = tuple(
            e.strip()
            for e in (self.config.subtensor.fallback_endpoints or "").split(",")
            if e.strip()
        )
        self.chain = ChainAccess(
            netuid=self.config.netuid,
            network=self.config.subtensor.network,
            fallback_endpoints=fallbacks,
        )

        self.hotkey = self.wallet.hotkey.ss58_address
        logger.info(f"Wallet   : {self.config.wallet.name}/{self.config.wallet.hotkey}")
        logger.info(f"Hotkey   : {self.hotkey}")
        logger.info(f"Network  : {self.config.subtensor.network}")

        self.metagraph: MetagraphSnapshot = self.chain.metagraph()
        self.check_registered()
        self.uid = self.metagraph.uid_of(self.hotkey)
        logger.info(
            f"Running {self.neuron_type} on netuid {self.config.netuid} as uid {self.uid}"
        )

        self.workdir = Path(self.config.workdir).expanduser()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.step = 0

    # -- lifecycle -------------------------------------------------------

    @abstractmethod
    def run(self) -> None:
        """The neuron's main loop."""

    def sync(self) -> None:
        """Keep local state aligned with the chain.

        Called at the top of every iteration. Ordering matters: a neuron that has
        been deregistered should discover that before it does any work.
        """
        if self.should_sync_metagraph():
            self.resync_metagraph()

        self.check_registered()

        if self.should_set_weights():
            self.set_weights()

        self.save_state()

    def check_registered(self) -> None:
        if not self.metagraph.is_registered(self.hotkey):
            raise NeuronNotRegistered(
                f"Hotkey {self.hotkey} is not registered on netuid "
                f"{self.config.netuid}. Register with:\n"
                f"  btcli subnet register --netuid {self.config.netuid} "
                f"--wallet.name {self.config.wallet.name} "
                f"--wallet.hotkey {self.config.wallet.hotkey}"
            )

    def should_sync_metagraph(self) -> bool:
        if self.uid is None:
            return True
        for neuron in self.metagraph.neurons:
            if neuron.uid == self.uid:
                return (self.block - neuron.last_update) > self.config.neuron.epoch_length
        return True

    def resync_metagraph(self) -> None:
        """Re-read the metagraph, keeping the previous one on failure.

        A transient RPC failure must not leave the neuron with no metagraph at
        all — that would turn a network blip into a crash loop. It is logged as
        an error rather than swallowed, because a *persistent* failure means this
        neuron is operating on stale data and someone needs to know.
        """
        logger.debug("Resyncing metagraph")
        try:
            self.metagraph = self.chain.metagraph()
        except ChainError as exc:
            logger.error(f"Metagraph resync failed, continuing on stale data: {exc}")
            return
        self.uid = self.metagraph.uid_of(self.hotkey)

    # The three hooks below are optional, not abstract: a miner legitimately sets
    # no weights and carries no state, and forcing it to declare empty overrides
    # would be noise.
    def should_set_weights(self) -> bool:
        """Overridden by the validator. Miners never set weights."""
        return False

    def set_weights(self) -> None:  # noqa: B027
        """Overridden by the validator."""
        return None

    def save_state(self) -> None:  # noqa: B027
        """Overridden where a neuron carries state across restarts."""
        return None

    def load_state(self) -> None:  # noqa: B027
        """Overridden where a neuron carries state across restarts."""
        return None
