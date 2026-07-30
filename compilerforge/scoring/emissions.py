"""Turning standings into a weight vector (10.6).

Under the June 2026 emission formula, each subnet's share of block TAO
emission is ``share_i = p_i × (1 − b_i) / Σ_j p_j × (1 − b_j)``, where ``b_i``
is the proportion of the last tempo's miner incentive withheld because it was
directed to subnet-owner hotkeys — counted whether that alpha was recycled or
burned. A subnet that burns 80% of miner incentive takes roughly an 80% cut to
its own TAO inflow, on top of paying nothing to miners.

Burning a cell when nobody beats the baseline is therefore self-harming, so the
policy is four rules:

  1. **Floor, do not burn.** When no artifact clears the improvement threshold,
     distribute across artifacts that passed every correctness gate and returned an
     honest null result, weighted by reliability history. This pays for verified
     correct work — which still has value — and keeps ``b_i`` near zero.
  2. **Decay the champion instead.** Stagnation reduces payout without redirecting
     anything to owner hotkeys.
  3. **Raise the bar, do not close the tap.** If the network stops improving, the
     answer is a harder corpus refresh, not a burn.
  4. **Burn is a safety valve.** Retained for an exploit, a corrupted corpus or a
     security incident, published with its reason every time. An incident, not a
     mechanism.

There is a further chain detail worth knowing: if an epoch ends with zero total
miner incentive, the miner half of that tempo's pending alpha is paid to
validators rather than withheld. A subnet that pays nothing to miners is not
saving anything — it is quietly transferring the miner half to validators while
cutting its own TAO inflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from compilerforge.scoring.aggregate import ArtifactAggregate
from compilerforge.scoring.dethronement import ChampionRegistry, select_specialist_top_k
from compilerforge.spec import SPEC

logger = logging.getLogger(__name__)


class Mechanism:
    """Chain-level scoring games.

    Each mechanism runs Yuma Consensus independently with its own weight matrix
    and separate bond pools. Miners keep a single UID across both, so an artifact
    can hold the generalist crown and simultaneously earn in a specialist cell
    without any custom accounting in the subnet code.
    """

    GENERALIST = 0
    SPECIALIST_AND_BOUNTY = 1


@dataclass(frozen=True, slots=True)
class ReliabilityRecord:
    """Per-artifact history that gates floor-pool eligibility.

    An artifact that fails a differential or sanitizer gate takes a reliability
    penalty; repeat offences shorten its eligibility window.
    """

    artifact_digest: str
    rounds_participated: int = 0
    correctness_failures: int = 0
    consecutive_clean_rounds: int = 0

    @property
    def reliability(self) -> float:
        """Weight applied to the floor distribution, in [0, 1]."""
        if self.rounds_participated == 0:
            return 0.0
        clean_rate = 1.0 - (self.correctness_failures / self.rounds_participated)
        return max(0.0, min(clean_rate, 1.0))

    @property
    def eligible(self) -> bool:
        """Repeat offenders leave the floor pool entirely."""
        return self.correctness_failures < 3


@dataclass(frozen=True, slots=True)
class BurnEvent:
    """Every activation of the safety valve is published with its reason."""

    round_number: int
    share: float
    reason: str
    at: datetime


@dataclass(slots=True)
class EmissionAllocator:
    """Produces the per-mechanism weight vectors a validator submits.

    The validator computes these from artifacts it measured itself. The operator
    never substitutes a centrally computed vector (7.3).
    """

    champions: ChampionRegistry = field(default_factory=ChampionRegistry)
    reliability: dict[str, ReliabilityRecord] = field(default_factory=dict)
    burn_log: list[BurnEvent] = field(default_factory=list)

    def allocate_generalist(
        self,
        aggregates: dict[str, ArtifactAggregate],
        *,
        hotkeys: dict[str, str],
    ) -> dict[str, float]:
        """Mechanism 0 — the generalist championship.

        Returns hotkey -> weight, normalised to sum to 1.0. The champion takes the
        bulk; the floor pool pays honest null results; the residual from champion
        decay flows to the floor rather than to a burn UID.
        """
        e = SPEC.emission
        champion = self.champions.champion
        weights: dict[str, float] = {}

        floor_share = e.floor_pool_share
        champion_share = 1.0 - floor_share

        if champion is not None:
            decay = champion.decay_multiplier()
            if decay < 1.0:
                logger.info(
                    "champion %s stagnant for %d rounds; share decayed to %.2f",
                    champion.artifact_digest[:19],
                    champion.rounds_without_improvement,
                    decay,
                )
            paid = champion_share * decay
            # Rule 2: the decayed remainder joins the floor pool. It is never
            # redirected to an owner hotkey, which would raise b_i.
            floor_share += champion_share - paid
            hotkey = champion.miner_hotkey or hotkeys.get(champion.artifact_digest, "")
            if hotkey:
                weights[hotkey] = weights.get(hotkey, 0.0) + paid
            else:
                floor_share += paid
        else:
            floor_share = 1.0

        floor = self._floor_distribution(aggregates, hotkeys, floor_share)
        for hotkey, weight in floor.items():
            weights[hotkey] = weights.get(hotkey, 0.0) + weight

        return _normalise(weights)

    def allocate_specialist_and_bounty(
        self,
        aggregates: dict[str, ArtifactAggregate],
        *,
        hotkeys: dict[str, str],
        commitments: dict[str, datetime],
        cells: list[str],
        bounty_awards: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Mechanism 1 — specialist cells (top-K) plus the bounty lane."""
        e = SPEC.emission
        weights: dict[str, float] = {}

        if cells:
            per_cell = e.specialist_share / len(cells)
            for cell in cells:
                ranked = select_specialist_top_k(aggregates, cell, commitments=commitments)
                if not ranked:
                    # An empty cell contributes its share to the floor pool, not
                    # to a burn UID.
                    continue
                # Geometric decay across the top-K: rank 1 earns meaningfully more
                # than rank 5 without rank 5 earning nothing.
                decay_weights = [0.8**i for i in range(len(ranked))]
                total = sum(decay_weights)
                for (digest, _score), dw in zip(ranked, decay_weights, strict=True):
                    hotkey = hotkeys.get(digest)
                    if hotkey:
                        weights[hotkey] = weights.get(hotkey, 0.0) + per_cell * dw / total

        if bounty_awards:
            total = sum(bounty_awards.values())
            if total > 0:
                for hotkey, amount in bounty_awards.items():
                    weights[hotkey] = weights.get(hotkey, 0.0) + e.bounty_share * amount / total

        # Rule 1 and 3: whatever the cells and bounties did not allocate goes to
        # the floor, keeping b_i near zero rather than closing the tap.
        allocated = sum(weights.values())
        if allocated < 1.0:
            floor = self._floor_distribution(aggregates, hotkeys, 1.0 - allocated)
            for hotkey, weight in floor.items():
                weights[hotkey] = weights.get(hotkey, 0.0) + weight

        return _normalise(weights)

    def _floor_distribution(
        self,
        aggregates: dict[str, ArtifactAggregate],
        hotkeys: dict[str, str],
        share: float,
    ) -> dict[str, float]:
        """Pay artifacts that passed every gate, weighted by reliability history.

        Includes both honest nulls and artifacts that improved something — the
        floor is not a consolation prize reserved for failure, it is the base
        payment for verified correct work.
        """
        if share <= 0:
            return {}

        eligible: dict[str, float] = {}
        for digest, agg in aggregates.items():
            hotkey = hotkeys.get(digest)
            if not hotkey:
                continue
            record = self.reliability.get(digest, ReliabilityRecord(digest))
            if not record.eligible:
                continue
            # Gate pass rate is the qualification; reliability history is the
            # weighting. An artifact that has never failed a correctness gate is
            # worth more to the network than one that fails intermittently.
            qualification = agg.gate_pass_rate
            if qualification <= 0:
                continue
            eligible[hotkey] = eligible.get(hotkey, 0.0) + qualification * max(
                record.reliability, 0.1
            )

        total = sum(eligible.values())
        if total <= 0:
            return {}
        return {hotkey: share * value / total for hotkey, value in eligible.items()}

    def record_round(self, aggregates: dict[str, ArtifactAggregate]) -> None:
        """Update reliability history after a round closes."""
        for digest, agg in aggregates.items():
            prior = self.reliability.get(digest, ReliabilityRecord(digest))
            failures = sum(1 for o in agg.outcomes if not o.gates_passed)
            self.reliability[digest] = ReliabilityRecord(
                artifact_digest=digest,
                rounds_participated=prior.rounds_participated + 1,
                correctness_failures=prior.correctness_failures + (1 if failures else 0),
                consecutive_clean_rounds=0 if failures else prior.consecutive_clean_rounds + 1,
            )

    def emergency_burn(
        self, *, share: float, reason: str, owner_hotkey: str, round_number: int
    ) -> dict[str, float]:
        """The safety valve. Treated as an incident, not as mechanism design.

        Disabled by default. Enabling it requires a spec change, and every
        activation is appended to a log that is published with the round.
        """
        if not SPEC.emission.emergency_burn_enabled:
            raise RuntimeError(
                "emergency burn is disabled in this consensus spec; enabling it is a "
                "spec change with an activation block, not an operational decision"
            )
        if not reason.strip():
            raise ValueError("an emergency burn without a published reason is not permitted")

        self.burn_log.append(
            BurnEvent(
                round_number=round_number,
                share=share,
                reason=reason,
                at=datetime.now(),
            )
        )
        logger.critical("EMERGENCY BURN %.1f%% — %s", share * 100, reason)
        return {owner_hotkey: share}


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in sorted(weights.items()) if v > 0}


def mechanism_split() -> dict[int, int]:
    """The u16 emission split across the two mechanisms.

    ``max_allowed_uids × mechanism_count ≤ 256`` constrains a two-mechanism subnet
    to at most 128 UIDs; that is enforced in the chain configuration, not here.
    """
    m0, m1 = SPEC.emission.mechanism_split_u16
    return {Mechanism.GENERALIST: m0, Mechanism.SPECIALIST_AND_BOUNTY: m1}
