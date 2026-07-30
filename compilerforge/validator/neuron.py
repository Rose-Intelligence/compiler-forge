"""The CompilerForge validator.

Two invariants the implementation must never break.

**Fail closed.** A missing task source, an unreadable artifact, a toolchain digest
mismatch or an incompatible consensus version produces *no score at all* — never a
guessed one. A validator that cannot measure something correctly is worth more to
the network silent than approximately right.

**Score locally, sign what you measured.** Nothing this neuron submits comes from
an operator-computed weight vector. Every number is derived from a measurement
this host made itself, which is the entire reason independent validation is worth
paying for.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from compilerforge.base.validator import BaseValidatorNeuron
from compilerforge.corpus.package import Corpus
from compilerforge.evaluation.baseline import BaselineBuilder
from compilerforge.evaluation.build import Builder
from compilerforge.evaluation.measurement import TierARunner, TierBRunner
from compilerforge.evaluation.pipeline import EvaluationContext
from compilerforge.sandbox.isolation import detect_runtime
from compilerforge.utils.config import NeuronConfig
from compilerforge.utils.logging import logger
from compilerforge.validator.forward import RoundAborted, forward


class Validator(BaseValidatorNeuron):
    """Runs evaluation rounds and submits weights on both mechanisms."""

    neuron_type = "ValidatorNeuron"

    def __init__(self, config: NeuronConfig | None = None) -> None:
        super().__init__(config=config)

        self.corpus_dir = Path(self.config.corpus.dir).expanduser()
        self.audit_dir = Path(self.config.audit.dir).expanduser()
        self.specialist_cells = [
            c.strip() for c in self.config.specialist.cells.split(",") if c.strip()
        ]

        self._builder = Builder()
        self._tier_a = TierARunner()
        self._preflight()

    # -- readiness -------------------------------------------------------

    def _preflight(self) -> None:
        """Refuse to start in a configuration that cannot produce valid scores."""
        if not self._tier_a.available():
            raise RuntimeError(
                "Valgrind is not installed. The deterministic tier is the "
                "consensus-bearing measurement; a validator without it must "
                "self-exclude rather than emit incompatible weights.\n"
                "  sudo apt-get install valgrind"
            )

        runtime = detect_runtime()
        if not runtime.is_hardened:
            message = (
                f"Container runtime is {runtime.value}, which shares the host kernel. "
                "This validator holds a hotkey on the same machine that runs "
                "untrusted miner code. Install gVisor (runsc) or Kata Containers."
            )
            if self.config.sandbox.allow_unhardened_runtime:
                logger.warning(message + " Continuing because the override is set.")
            else:
                raise RuntimeError(
                    message + "\nPass --sandbox.allow_unhardened_runtime for local "
                    "development only."
                )

        if not self.corpus_dir.exists():
            raise RuntimeError(f"Corpus directory {self.corpus_dir} does not exist")

        if self.config.sandbox.verify_in_sandbox:
            image = self.config.sandbox.verify_image
            if not image:
                raise RuntimeError(
                    "--sandbox.verify_in_sandbox requires --sandbox.verify_image: "
                    "the container whose toolchain is canonical for the fleet."
                )
            digest = self._pin_sandbox_toolchain(image)
            os.environ["CF_TOOLCHAIN_DIGEST"] = digest
            logger.info("Verification runs in %s; toolchain pinned to %s", image, digest[:26])

        if not self.config.measurement.tier_b:
            logger.info(
                "Wall-clock measurement is disabled. This validator contributes a "
                "full consensus weight through the deterministic tier and no "
                "wall-clock evidence."
            )

    def _pin_sandbox_toolchain(self, image: str) -> str:
        """The toolchain digest computed inside the verification image.

        Making the container's toolchain canonical: every validator running this
        image derives tasks and stamps artifacts with the same digest, so a
        measurement made in the image is comparable across the fleet regardless of
        the host compiler.
        """
        proc = subprocess.run(  # noqa: S603
            [
                self.config.sandbox.container_cli, "run", "--rm", "--network=none",
                image, "python3", "-c",
                "from compilerforge.evaluation.build import toolchain_digest;"
                " print(toolchain_digest())",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        lines = proc.stdout.strip().splitlines()
        digest = lines[-1].strip() if lines else ""
        if not digest.startswith("sha256:"):
            raise RuntimeError(
                f"could not read the toolchain digest from {image!r}: "
                f"{(proc.stderr or proc.stdout)[-500:]}"
            )
        return digest

    def evaluation_context(self, corpus: Corpus) -> EvaluationContext:
        """Assemble the validator-owned tooling for one round."""
        tier_b = (
            TierBRunner(
                cpu_affinity=self.config.measurement.tier_b_affinity,
                require_calibration=True,
            )
            if self.config.measurement.tier_b
            else None
        )
        return EvaluationContext(
            workdir=self.workdir / "eval",
            builder=self._builder,
            tier_a=self._tier_a,
            tier_b=tier_b,
            baselines=BaselineBuilder(
                builder=self._builder,
                tier_a=self._tier_a,
                cache_dir=self.workdir / "baselines",
            ),
            corpus_snapshot=corpus.snapshot_id,
            verifier_hotkey=self.wallet.hotkey.ss58_address,
            tier_b_available=tier_b is not None,
            fuzz_seconds=self.config.measurement.fuzz_seconds,
        )

    # -- main loop -------------------------------------------------------

    async def forward(self):
        return await forward(self)

    def run(self, once: bool = False) -> None:
        """Run rounds until interrupted."""
        logger.info(f"Validator starting on netuid {self.config.netuid} as uid {self.uid}")
        self.start_heartbeat()

        try:
            while True:
                started = time.monotonic()
                try:
                    self.sync()
                    asyncio.run(self.forward())
                except KeyboardInterrupt:
                    raise
                except RoundAborted as exc:
                    # A round that could not run. Stated plainly, because "no
                    # weights this round" must never be indistinguishable from
                    # "everything went fine".
                    logger.error(f"Round {self.round_number} aborted: {exc}")
                except Exception as exc:  # noqa: BLE001 - a bad round must not kill the neuron
                    logger.exception(f"Round {self.round_number} failed: {exc}")
                finally:
                    self.save_state()

                if once:
                    return

                self.step += 1
                # A round shorter than the tempo waits out the remainder; a longer
                # one starts the next immediately rather than accumulating drift.
                elapsed = time.monotonic() - started
                remaining = self.config.neuron.epoch_length * 12 - elapsed
                if remaining > 0:
                    logger.info(f"Round complete; next round in {remaining / 60:.0f} minutes")
                    time.sleep(remaining)
        except KeyboardInterrupt:
            logger.info("Validator stopped by operator")
        finally:
            self.stop_heartbeat()
            self.save_state()

    def should_set_weights(self) -> bool:
        """Weights are set at the end of each round, not on the sync tick.

        Returning False here keeps the base class from submitting a stale vector
        between rounds; the heartbeat handles liveness instead.
        """
        return False
