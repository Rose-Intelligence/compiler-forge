"""The CompilerForge miner.

A miner in this subnet does not answer queries. It publishes one immutable
artifact — a container image pinned by digest — and validators pull that artifact
and run it against repositories the miner has never seen.

That makes the neuron's job small and its discipline important:

* Commit a **digest**, never a tag. A tag can be moved after the fact, which would
  defeat the entire freeze-before-entropy guarantee the network rests on.
* Commit **before** the round you want to compete in. An artifact committed after
  the task-selecting block is not eligible, by construction.
* Never self-report a speedup. Numbers in the agent's report are informational;
  the validator's measurements are the only ones that count.
"""

from __future__ import annotations

import time

from compilerforge.base.miner import ArtifactResolutionError, BaseMinerNeuron
from compilerforge.utils.logging import logger
from compilerforge.utils.misc import short_digest


class Miner(BaseMinerNeuron):
    """Keeps this hotkey's artifact commitment current."""

    neuron_type = "MinerNeuron"

    def run(self, once: bool = False) -> None:
        """Commit the configured artifact and keep the commitment alive.

        There is no serving loop. The neuron stays running so that it re-commits
        after a chain reorg or a metagraph change, and so that operators have one
        process to supervise.
        """
        logger.info(f"Miner starting on netuid {self.config.netuid} as uid {self.uid}")

        try:
            commitment = self.build_commitment()
        except ArtifactResolutionError as exc:
            logger.error(str(exc))
            return

        logger.info(f"Artifact: {commitment.pull_reference()}")
        logger.info(f"Cells:    {', '.join(commitment.cells)}")

        try:
            while True:
                self.sync()

                on_chain = self.current_commitment()
                if on_chain is None or on_chain.digest != commitment.digest:
                    self.commit(commitment)
                else:
                    logger.info(
                        f"Artifact {short_digest(commitment.digest)} is live on chain"
                    )

                if once:
                    return

                self.step += 1
                time.sleep(self.config.neuron.epoch_length * 12)
        except KeyboardInterrupt:
            logger.info("Miner stopped by operator")
        finally:
            self.save_state()
