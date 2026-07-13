"""Cross-task aggregation.

Aggregate in log space — geometric mean of ``1 + capture`` — so one enormous win
cannot mask repeated failures. Publish the generalist score and the per-cell
specialist scores separately, because an artifact must not win the generalist
crown by dominating a single library family.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from compilerforge.evaluation.statistics import geometric_mean_of_one_plus, standard_error_of_mean
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.scoring.capture import cross_validator_agreement_score
from compilerforge.spec import SPEC


class IncomparableScores(RuntimeError):
    """Scores from different specification versions are never mixed."""


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """One artifact's result on one task, after combining every validator's view."""

    task_id: str
    package_id: str
    family: str
    hidden: bool
    capture: float
    score: float
    #: How many independent validators produced a comparable artifact for this pair.
    verifier_count: int
    agreement: float
    honest_null: bool
    gates_passed: bool


@dataclass(frozen=True, slots=True)
class ArtifactAggregate:
    """One artifact's standing across the whole round."""

    artifact_digest: str
    outcomes: tuple[TaskOutcome, ...]

    @property
    def scored_outcomes(self) -> tuple[TaskOutcome, ...]:
        return tuple(o for o in self.outcomes if o.gates_passed)

    @property
    def generalist_score(self) -> float:
        """Geometric mean of (1 + capture) across every task in the round."""
        return geometric_mean_of_one_plus([o.capture for o in self.outcomes])

    @property
    def hidden_capture(self) -> float:
        """Stage 6. Scores rising on public tasks while this stays flat is the
        early-warning signal for benchmark overfitting."""
        hidden = [o.capture for o in self.outcomes if o.hidden]
        return geometric_mean_of_one_plus(hidden) if hidden else 0.0

    @property
    def public_capture(self) -> float:
        public = [o.capture for o in self.outcomes if not o.hidden]
        return geometric_mean_of_one_plus(public) if public else 0.0

    @property
    def gate_pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.gates_passed) / len(self.outcomes)

    @property
    def honest_null_rate(self) -> float:
        """Passed every correctness gate and returned no improvement.

        This is the population the floor pool pays. It is a
        respectable outcome — an honest empty result is scored above a rejected
        one — and it must be tracked separately from failure.
        """
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.honest_null) / len(self.outcomes)

    @property
    def standard_error(self) -> float:
        """Feeds the dethronement gap: more evidence, smaller required margin."""
        return standard_error_of_mean([o.capture for o in self.outcomes])

    def cell_scores(self) -> dict[str, float]:
        """Per-family scores, so a single library family cannot carry an artifact."""
        by_family: dict[str, list[float]] = defaultdict(list)
        for o in self.outcomes:
            by_family[o.family].append(o.capture)
        return {
            family: geometric_mean_of_one_plus(captures)
            for family, captures in sorted(by_family.items())
        }


@dataclass(slots=True)
class RoundAggregator:
    """Combines every validator's score artifacts into per-artifact standings.

    Each validator scores locally and signs what it measured. This
    class never computes a weight vector on anyone's behalf — it reduces the
    artifacts *one* validator holds, including the ones it received from peers for
    the cross-validator agreement component, into that validator's own view.
    """

    spec_digest: str = field(default_factory=SPEC.digest)
    #: (artifact_digest, task_id) -> artifacts from every validator seen
    _by_pair: dict[tuple[str, str], list[ScoreArtifact]] = field(default_factory=dict)
    _task_meta: dict[str, tuple[str, str, bool]] = field(default_factory=dict)

    def register_task(self, task_id: str, package_id: str, family: str, hidden: bool) -> None:
        self._task_meta[task_id] = (package_id, family, hidden)

    def add(self, artifact: ScoreArtifact) -> None:
        if artifact.spec_digest != self.spec_digest:
            raise IncomparableScores(
                f"artifact was scored under spec {artifact.spec_digest}, "
                f"this round runs {self.spec_digest}"
            )
        if artifact.voided:
            return
        key = (artifact.artifact_digest, artifact.task_id)
        self._by_pair.setdefault(key, []).append(artifact)

    def void_task(self, task_id: str) -> None:
        """Drop a task for every artifact. No crown change on a voided round."""
        for key in [k for k in self._by_pair if k[1] == task_id]:
            del self._by_pair[key]

    def aggregate(self) -> dict[str, ArtifactAggregate]:
        by_artifact: dict[str, list[TaskOutcome]] = defaultdict(list)

        for (digest, task_id), artifacts in sorted(self._by_pair.items()):
            package_id, family, hidden = self._task_meta.get(task_id, ("unknown", "unknown", False))

            # Independent measurements of the same patch. The median is the
            # consensus view; the spread is the agreement component.
            speedups = [
                a.tier_a.deterministic_speedup for a in artifacts if a.tier_a is not None
            ]
            agreement = cross_validator_agreement_score(speedups)

            captures = sorted(
                a.reference.capture for a in artifacts if a.reference is not None
            )
            capture = captures[len(captures) // 2] if captures else 0.0

            scores = sorted(a.score for a in artifacts)
            score = scores[len(scores) // 2] if scores else 0.0
            # Re-award the agreement component now that peers are known; it is the
            # one component a single validator cannot compute alone.
            score += SPEC.components.cross_validator_agreement * agreement

            gates_passed = all(a.all_gates_passed() for a in artifacts)
            honest_null = gates_passed and all(a.honest_null for a in artifacts)

            by_artifact[digest].append(
                TaskOutcome(
                    task_id=task_id,
                    package_id=package_id,
                    family=family,
                    hidden=hidden,
                    capture=capture if gates_passed else 0.0,
                    score=round(score, 6) if gates_passed else 0.0,
                    verifier_count=len({a.verifier_hotkey for a in artifacts}),
                    agreement=agreement,
                    honest_null=honest_null,
                    gates_passed=gates_passed,
                )
            )

        return {
            digest: ArtifactAggregate(artifact_digest=digest, outcomes=tuple(outcomes))
            for digest, outcomes in sorted(by_artifact.items())
        }
