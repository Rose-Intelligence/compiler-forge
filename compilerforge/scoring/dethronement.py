"""Champion and challenger.

The commercial product deploys exactly one default agent, so the mechanism should
select exactly one — with a meaningful margin and a short memory of recent
challengers, on a continuous dominance signal rather than a binary one.

Four rules, and every one of them is load-bearing:

  **Dominance band.** The challenger must exceed the champion by a margin ``m`` on
  the aggregate score *and* must not be materially worse on any scored cell within
  a tolerance ``τ``. Without the per-cell clause an artifact could win the crown by
  dominating a single library family.

  **A statistical margin that shrinks with evidence.** Few samples demand a large
  gap; many samples relax toward the floor. This is what makes a near-copy of the
  champion unable to dethrone it — a copy matches on shared tasks and can never
  clear a positive margin everywhere.

  **Defender advantage.** The crown changes only at a decisive checkpoint after
  warm-up rounds, never on a single round, and never on a round whose baseline
  stability gate fired.

  **Tie-break by commitment order.** The earlier on-chain commitment wins, which
  removes the incentive to watch and clone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from compilerforge.scoring.aggregate import ArtifactAggregate
from compilerforge.spec import SPEC


@dataclass(frozen=True, slots=True)
class Champion:
    artifact_digest: str
    miner_hotkey: str
    committed_at: datetime
    #: Aggregate at the time the crown was won.
    crowned_score: float
    crowned_round: int
    #: Rounds since the champion last improved its own aggregate.
    rounds_without_improvement: int = 0

    def decay_multiplier(self) -> float:
        """Stagnation reduces payout without redirecting anything to owner hotkeys.

        A champion that has not been beaten and has not improved in N rounds sees
        its share decay toward the floor distribution. Burning would cut the
        subnet's own TAO inflow; decaying does not.
        """
        e = SPEC.emission
        overdue = self.rounds_without_improvement - e.champion_stagnation_rounds
        if overdue <= 0:
            return 1.0
        return max(e.champion_decay_min_share, 1.0 - e.champion_decay_per_round * overdue)


@dataclass(frozen=True, slots=True)
class ChallengeResult:
    challenger_digest: str
    dethroned: bool
    aggregate_gap: float
    required_gap: float
    failing_cells: tuple[str, ...] = ()
    reason: str = ""


@dataclass(slots=True)
class ChampionRegistry:
    """Tracks the crown across rounds and decides when it changes hands."""

    champion: Champion | None = None
    round_number: int = 0
    #: digest -> consecutive rounds this challenger has cleared the band.
    _sustained: dict[str, int] = field(default_factory=dict)

    def required_gap(self, challenger: ArtifactAggregate) -> float:
        """clamp(z * SE, floor, ceiling) — evidence buys a smaller required gap."""
        d = SPEC.dethronement
        se = challenger.standard_error
        if se == float("inf"):
            return d.gap_ceiling
        return max(d.gap_floor, min(d.z * se, d.gap_ceiling))

    def evaluate(
        self,
        aggregates: dict[str, ArtifactAggregate],
        *,
        commitments: dict[str, datetime],
        hotkeys: dict[str, str],
        round_was_noisy: bool = False,
    ) -> ChallengeResult | None:
        """Decide whether the crown changes this round.

        Returns the winning challenge, or ``None`` when the champion holds.
        """
        self.round_number += 1

        if not aggregates:
            return None

        if self.champion is None:
            # Bootstrapping: the highest aggregate takes the crown, with the
            # earliest commitment breaking ties.
            best = self._best(aggregates, commitments)
            self.champion = Champion(
                artifact_digest=best.artifact_digest,
                miner_hotkey=hotkeys.get(best.artifact_digest, ""),
                committed_at=commitments.get(best.artifact_digest, datetime.min),
                crowned_score=best.generalist_score,
                crowned_round=self.round_number,
            )
            return ChallengeResult(
                challenger_digest=best.artifact_digest,
                dethroned=True,
                aggregate_gap=best.generalist_score,
                required_gap=0.0,
                reason="initial champion",
            )

        if round_was_noisy:
            # No crown change on a round whose baseline stability gate fired.
            self._advance_champion(aggregates)
            return None

        incumbent = aggregates.get(self.champion.artifact_digest)
        incumbent_score = (
            incumbent.generalist_score if incumbent is not None else self.champion.crowned_score
        )
        incumbent_cells = incumbent.cell_scores() if incumbent is not None else {}

        best_challenge: ChallengeResult | None = None
        for digest, agg in sorted(aggregates.items()):
            if digest == self.champion.artifact_digest:
                continue
            result = self._challenge(agg, incumbent_score, incumbent_cells)
            if not result.dethroned:
                self._sustained.pop(digest, None)
                continue

            # Defender advantage: clearing the band once is not enough.
            self._sustained[digest] = self._sustained.get(digest, 0) + 1
            if self._sustained[digest] < SPEC.dethronement.warmup_rounds:
                continue

            if best_challenge is None or result.aggregate_gap > best_challenge.aggregate_gap:
                best_challenge = result
            elif abs(result.aggregate_gap - best_challenge.aggregate_gap) < 1e-9:
                # Tie-break by commitment order: watching and cloning earns nothing.
                if commitments.get(digest, datetime.max) < commitments.get(
                    best_challenge.challenger_digest, datetime.max
                ):
                    best_challenge = result

        if best_challenge is None:
            self._advance_champion(aggregates)
            return None

        winner = aggregates[best_challenge.challenger_digest]
        self.champion = Champion(
            artifact_digest=winner.artifact_digest,
            miner_hotkey=hotkeys.get(winner.artifact_digest, ""),
            committed_at=commitments.get(winner.artifact_digest, datetime.min),
            crowned_score=winner.generalist_score,
            crowned_round=self.round_number,
        )
        self._sustained.clear()
        return best_challenge

    def _challenge(
        self,
        challenger: ArtifactAggregate,
        incumbent_score: float,
        incumbent_cells: dict[str, float],
    ) -> ChallengeResult:
        d = SPEC.dethronement
        gap = challenger.generalist_score - incumbent_score
        required = max(d.margin, self.required_gap(challenger))

        if gap < required:
            return ChallengeResult(
                challenger_digest=challenger.artifact_digest,
                dethroned=False,
                aggregate_gap=gap,
                required_gap=required,
                reason=f"aggregate gap {gap:.4f} below required {required:.4f}",
            )

        # Must not be materially worse on any scored cell.
        challenger_cells = challenger.cell_scores()
        failing = tuple(
            sorted(
                cell
                for cell, incumbent_value in incumbent_cells.items()
                if challenger_cells.get(cell, 0.0) < incumbent_value - d.cell_tolerance
            )
        )
        if failing:
            return ChallengeResult(
                challenger_digest=challenger.artifact_digest,
                dethroned=False,
                aggregate_gap=gap,
                required_gap=required,
                failing_cells=failing,
                reason=f"materially worse on {', '.join(failing)}",
            )

        return ChallengeResult(
            challenger_digest=challenger.artifact_digest,
            dethroned=True,
            aggregate_gap=gap,
            required_gap=required,
            reason="cleared the dominance band on aggregate and every cell",
        )

    def _advance_champion(self, aggregates: dict[str, ArtifactAggregate]) -> None:
        """Track whether the incumbent is still improving, for the decay rule."""
        if self.champion is None:
            return
        current = aggregates.get(self.champion.artifact_digest)
        if current is not None and current.generalist_score > self.champion.crowned_score + 1e-9:
            self.champion = Champion(
                artifact_digest=self.champion.artifact_digest,
                miner_hotkey=self.champion.miner_hotkey,
                committed_at=self.champion.committed_at,
                crowned_score=current.generalist_score,
                crowned_round=self.champion.crowned_round,
                rounds_without_improvement=0,
            )
        else:
            self.champion = Champion(
                artifact_digest=self.champion.artifact_digest,
                miner_hotkey=self.champion.miner_hotkey,
                committed_at=self.champion.committed_at,
                crowned_score=self.champion.crowned_score,
                crowned_round=self.champion.crowned_round,
                rounds_without_improvement=self.champion.rounds_without_improvement + 1,
            )

    @staticmethod
    def _best(
        aggregates: dict[str, ArtifactAggregate], commitments: dict[str, datetime]
    ) -> ArtifactAggregate:
        return min(
            aggregates.values(),
            key=lambda a: (-a.generalist_score, commitments.get(a.artifact_digest, datetime.max)),
        )


def select_specialist_top_k(
    aggregates: dict[str, ArtifactAggregate],
    cell: str,
    *,
    commitments: dict[str, datetime],
    k: int | None = None,
) -> list[tuple[str, float]]:
    """Specialist cells reward top-K rather than one champion.

    Multiple deployment targets are simultaneously useful — the best agent
    legitimately differs by language, workload and hardware — so a cell structure
    captures what a single universal champion would average away.
    """
    top_k = k if k is not None else SPEC.dethronement.specialist_top_k
    ranked = sorted(
        (
            (digest, agg.cell_scores().get(cell, 0.0))
            for digest, agg in aggregates.items()
            if cell in agg.cell_scores()
        ),
        key=lambda item: (-item[1], commitments.get(item[0], datetime.max)),
    )
    return [(digest, score) for digest, score in ranked[:top_k] if score > 0]
