"""Base validator neuron.

Adds to :class:`BaseNeuron` the three things a CompilerForge validator needs
beyond generic chain plumbing:

* **Two weight vectors, not one.** The generalist championship and the
  specialist/bounty lane are separate scoring games running Yuma Consensus
  independently, each with its own weight matrix and bond pool. They are
  addressed by ``mechid`` on ``set_weights``.

* **A weight heartbeat.** A validator whose last weight update predates the
  activity cutoff is excluded from consensus for that epoch. With a round that
  spans a full day, that will happen unless something re-asserts the last commit
  every few hours.

* **Durable state.** The champion registry and per-artifact reliability history
  span rounds, so a restart must not silently reset the crown.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from compilerforge.base.neuron import BaseNeuron
from compilerforge.chain.access import ChainError
from compilerforge.scoring.dethronement import Champion
from compilerforge.scoring.emissions import (
    EmissionAllocator,
    Mechanism,
    ReliabilityRecord,
    mechanism_split,
)
from compilerforge.utils.config import NeuronConfig, add_validator_args
from compilerforge.utils.logging import logger


class BaseValidatorNeuron(BaseNeuron):
    """Weight setting, heartbeat and persistence for a validator."""

    neuron_type: str = "ValidatorNeuron"

    @classmethod
    def add_args(cls, parser) -> None:
        super().add_args(parser)
        add_validator_args(cls, parser)

    def __init__(self, config: NeuronConfig | None = None) -> None:
        super().__init__(config=config)

        self.allocator = EmissionAllocator()
        self.round_number = 0
        self.last_weights: dict[int, dict[str, float]] = {}
        self.last_weight_block = 0

        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._lock = threading.RLock()

        self.load_state()

    # -- weights ---------------------------------------------------------

    def set_weights_for_mechanism(
        self, weights_by_hotkey: dict[str, float], mechid: int
    ) -> bool:
        """Submit one mechanism's weight vector.

        Commit-reveal is a subnet hyperparameter rather than a call flag: the SDK
        picks plaintext or commit-reveal from it and performs the later reveal
        itself, so nothing here reimplements that choice.

        Returns False only for reasons this validator can see and explain. A
        chain-side rejection raises, because a weight vector that was refused and
        reported as set is the worst of both outcomes.
        """
        if not weights_by_hotkey:
            logger.warning(f"No weights to set on mechanism {mechid}; skipping")
            return False

        uids_by_hotkey = self.metagraph.hotkeys()
        resolved: list[tuple[int, float]] = []
        unregistered: list[str] = []
        for hotkey, weight in sorted(weights_by_hotkey.items()):
            uid = uids_by_hotkey.get(hotkey)
            if uid is None:
                unregistered.append(hotkey)
            elif weight > 0:
                resolved.append((uid, weight))

        if unregistered:
            logger.warning(
                f"Skipping {len(unregistered)} unregistered hotkeys on mechanism {mechid}"
            )

        if not resolved:
            logger.error(
                "No requested hotkey is registered; leaving on-chain weights unchanged"
            )
            return False

        total = sum(w for _, w in resolved)
        uids = [uid for uid, _ in resolved]
        values = [w / total for _, w in resolved]

        self.chain.set_weights(
            self.wallet,
            uids=uids,
            weights=values,
            mechid=mechid,
            version_key=self.spec_version,
        )
        logger.success(f"Set weights for {len(uids)} uids on mechanism {mechid}")
        return True

    def submit_round_weights(
        self, generalist: dict[str, float], specialist: dict[str, float]
    ) -> dict[int, bool]:
        """Submit each mechanism's weight vector, folding lanes the subnet cannot pay.

        The two-mechanism design runs the generalist championship and the
        specialist/bounty lane as separate on-chain scoring games. A subnet starts
        with one mechanism, though, and the second exists only once the owner
        raises ``mechanism_count``. Discarding the specialist/bounty vector while
        that lane has nowhere to land would pay nothing to a miner whose value is
        specialisation — a miner that is scored, and wins its cells, yet earns
        zero. Instead its vector is folded into mechanism 0, each lane scaled by
        its designed share of total emission, so every scored miner earns from the
        one mechanism that exists while the intended generalist/specialist emphasis
        is preserved. Once the lane is created the two vectors separate again with
        no change here.

        One mechanism failing does not prevent the other from being submitted, and
        only vectors the chain accepted are remembered for the heartbeat to
        re-assert; heart-beating a rejected vector would repeat the failure in
        silence.
        """
        results: dict[int, bool] = {}
        landed: dict[int, dict[str, float]] = {}

        available = self.chain.mechanism_count()
        split = mechanism_split()
        total = sum(split.values()) or 1

        # Build the vector actually submitted per existing mechanism. A lane whose
        # mechanism the chain has not created is merged into mechanism 0; each lane
        # is scaled by its share of total emission so the merge keeps the designed
        # emphasis. For a lane submitted on its own the scale washes out when
        # set_weights normalises, so this changes nothing until a fold happens.
        to_submit: dict[int, dict[str, float]] = {}
        folded: list[int] = []
        for mechid, weights in (
            (Mechanism.GENERALIST, generalist),
            (Mechanism.SPECIALIST_AND_BOUNTY, specialist),
        ):
            if not weights:
                continue
            target = mechid if mechid < available else Mechanism.GENERALIST
            if target != mechid:
                folded.append(mechid)
            scale = split.get(mechid, 0) / total
            bucket = to_submit.setdefault(target, {})
            for hotkey, weight in weights.items():
                bucket[hotkey] = bucket.get(hotkey, 0.0) + scale * weight

        if folded:
            logger.info(
                f"Subnet runs {available} mechanism(s); folded lane(s) {folded} into "
                f"mechanism 0 by emission share — {len(to_submit.get(Mechanism.GENERALIST, {}))} "
                "miners weighted on the one mechanism that exists"
            )

        for mechid, weights in sorted(to_submit.items()):
            try:
                results[mechid] = self.set_weights_for_mechanism(weights, mechid)
            except ChainError as exc:
                logger.error(f"Mechanism {mechid} weights rejected by the chain: {exc}")
                results[mechid] = False
                continue
            if results[mechid]:
                landed[mechid] = weights

        with self._lock:
            # Only re-assert what the chain accepted. Heart-beating a vector that
            # was rejected would repeat the failure every few hours in silence.
            self.last_weights.update(landed)
            self.last_weight_block = self.block

        return results

    # -- heartbeat -------------------------------------------------------

    def start_heartbeat(self) -> None:
        """Re-assert the last weight vector between rounds.

        Without this a validator that scores once a day drops out of consensus
        long before its next round, for reasons that have nothing to do with the
        quality of its measurements.
        """
        if self._heartbeat_thread is not None:
            return

        interval_seconds = self.config.neuron.heartbeat_blocks * 12

        def loop() -> None:
            while not self._heartbeat_stop.wait(interval_seconds):
                with self._lock:
                    pending = dict(self.last_weights)
                if not pending:
                    continue
                for mechid, weights in pending.items():
                    try:
                        self.set_weights_for_mechanism(weights, mechid)
                    except ChainError as exc:
                        # The heartbeat exists to keep this validator inside the
                        # activity cutoff, so it must survive a failed beat — but
                        # a persistent failure means the validator is drifting
                        # out of consensus and has to be visible.
                        logger.error(f"Heartbeat rejected on mechanism {mechid}: {exc}")
                    except Exception as exc:  # noqa: BLE001 - never kill the heartbeat
                        logger.exception(
                            f"Unexpected heartbeat failure on mechanism {mechid}: {exc}"
                        )

        self._heartbeat_thread = threading.Thread(
            target=loop, name="cf-weight-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()
        logger.info(f"Weight heartbeat every {interval_seconds}s")

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=30)
            self._heartbeat_thread = None

    # -- persistence -----------------------------------------------------

    @property
    def state_path(self) -> Path:
        return Path(self.config.neuron.full_path) / "state.json"

    def save_state(self) -> None:
        """Persist the crown and reliability history.

        A restart that resets the champion would hand the crown to whoever happens
        to score highest in the next round, discarding the defender advantage that
        makes cloning unprofitable.
        """
        champion = self.allocator.champions.champion
        payload = {
            "round_number": self.round_number,
            "champion": (
                {
                    "artifact_digest": champion.artifact_digest,
                    "miner_hotkey": champion.miner_hotkey,
                    "committed_at": champion.committed_at.isoformat(),
                    "crowned_score": champion.crowned_score,
                    "crowned_round": champion.crowned_round,
                    "rounds_without_improvement": champion.rounds_without_improvement,
                }
                if champion
                else None
            ),
            "champion_round_number": self.allocator.champions.round_number,
            "reliability": {
                digest: {
                    "rounds_participated": r.rounds_participated,
                    "correctness_failures": r.correctness_failures,
                    "consecutive_clean_rounds": r.consecutive_clean_rounds,
                }
                for digest, r in self.allocator.reliability.items()
            },
            "last_weights": {str(k): v for k, v in self.last_weights.items()},
            "saved_at": time.time(),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.state_path)

    def load_state(self) -> None:
        if not self.state_path.exists():
            logger.info("No saved state; starting from an empty champion registry")
            return
        try:
            payload = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Refuse to start rather than silently resetting. The champion
            # registry and reliability history are consensus-relevant: starting
            # "fresh" would hand the crown to whoever scores highest next round,
            # discarding the defender advantage that makes cloning unprofitable,
            # and it would do so invisibly.
            raise RuntimeError(
                f"Saved validator state at {self.state_path} is unreadable: {exc}\n"
                "This file holds the champion registry and per-artifact reliability "
                "history. Starting without it would silently reset the crown, so "
                "this is fatal. Restore the file from a backup, or delete it to "
                "deliberately start from an empty registry."
            ) from exc

        self.round_number = int(payload.get("round_number", 0))
        self.allocator.champions.round_number = int(payload.get("champion_round_number", 0))

        champion = payload.get("champion")
        if champion:
            self.allocator.champions.champion = Champion(
                artifact_digest=champion["artifact_digest"],
                miner_hotkey=champion["miner_hotkey"],
                committed_at=datetime.fromisoformat(champion["committed_at"]),
                crowned_score=float(champion["crowned_score"]),
                crowned_round=int(champion["crowned_round"]),
                rounds_without_improvement=int(champion["rounds_without_improvement"]),
            )

        self.allocator.reliability = {
            digest: ReliabilityRecord(
                artifact_digest=digest,
                rounds_participated=int(r["rounds_participated"]),
                correctness_failures=int(r["correctness_failures"]),
                consecutive_clean_rounds=int(r["consecutive_clean_rounds"]),
            )
            for digest, r in (payload.get("reliability") or {}).items()
        }
        self.last_weights = {
            int(k): v for k, v in (payload.get("last_weights") or {}).items()
        }
        logger.info(
            f"Restored state: round {self.round_number}, "
            f"{len(self.allocator.reliability)} tracked artifacts"
        )
