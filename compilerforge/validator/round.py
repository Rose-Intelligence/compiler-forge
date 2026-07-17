"""One evaluation round, end to end.

The cost structure of a round drives the shape of this file:

    If every validator independently executes every agent, a round consumes
    roughly 1,000 agent runs x ~150,000 tokens per validator per round, duplicated
    across the entire validator set, daily. That is the single largest cost in the
    system and it buys almost nothing, because the expensive part — generating the
    patch — is not the part consensus needs to be independent about.

So agent execution happens **once** per (artifact, task), by a seeded rotating
producer subset. The resulting patch and its digest are published. Every validator
then independently applies, builds, differentially tests, fuzzes, sanitizes and
measures *that patch*. Consensus is formed over the measurement, which is exactly
where independence matters and where cost is low.

Anti-collusion: a random sample of pairs is re-executed each round by a different
producer under the same seed. A producer whose published patch cannot be reproduced
loses eligibility and its stake weight is challenged.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.chain.commitments import FrozenArtifact
from compilerforge.evaluation.differential import DifferentialCase, generate_cases
from compilerforge.evaluation.pipeline import (
    CandidatePatch,
    EvaluationContext,
    Evaluator,
    TaskVoided,
)
from compilerforge.evaluation.selection import RoundPlan, SelectedTask
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.scoring.aggregate import RoundAggregator
from compilerforge.spec import SPEC

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProducerAssignment:
    """Who executes which (artifact, task) pair this round."""

    artifact_digest: str
    task_id: str
    producer_uid: int
    #: A second producer re-runs this pair under the same seed to check the first.
    audit_producer_uid: int | None = None


def assign_producers(
    plan: RoundPlan,
    artifacts: list[FrozenArtifact],
    *,
    audit_fraction: float = 0.05,
) -> list[ProducerAssignment]:
    """Deterministically distribute agent execution across the validator set.

    Every validator derives the same assignment from the same block hash, so no
    coordination round-trip is needed and nobody chooses which pairs they produce.
    """
    if not artifacts:
        return []

    uids = sorted({a.uid for a in artifacts})
    assignments: list[ProducerAssignment] = []

    for task in plan.tasks:
        for artifact in sorted(artifacts, key=lambda a: a.digest):
            draw = plan.seed.stream(f"producer|{artifact.digest}|{task.task.task_id}")
            producer = uids[draw % len(uids)]

            audit_producer: int | None = None
            # Fixed-point comparison so the audit sample is identical everywhere.
            if (draw >> 128) % 10_000 < int(audit_fraction * 10_000) and len(uids) > 1:
                candidates = [u for u in uids if u != producer]
                audit_producer = candidates[(draw >> 160) % len(candidates)]

            assignments.append(
                ProducerAssignment(
                    artifact_digest=artifact.digest,
                    task_id=task.task.task_id,
                    producer_uid=producer,
                    audit_producer_uid=audit_producer,
                )
            )
    return assignments


@dataclass(frozen=True, slots=True)
class ProducedPatch:
    """A patch published by its producer, ready for everyone to verify."""

    artifact_digest: str
    task_id: str
    producer_uid: int
    patch: str
    patch_digest: str

    @staticmethod
    def digest_of(patch: str) -> str:
        return "sha256:" + hashlib.sha256(patch.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReproductionChallenge:
    """A producer whose published patch could not be reproduced."""

    producer_uid: int
    artifact_digest: str
    task_id: str
    published_digest: str
    reproduced_digest: str

    @property
    def detail(self) -> str:
        return (
            f"producer uid {self.producer_uid} published patch {self.published_digest[:19]} "
            f"for {self.artifact_digest[:19]} but re-execution under the same seed produced "
            f"{self.reproduced_digest[:19]}"
        )


@dataclass(slots=True)
class RoundResult:
    round_number: int
    plan: RoundPlan
    score_artifacts: list[ScoreArtifact] = field(default_factory=list)
    voided_tasks: dict[str, str] = field(default_factory=dict)
    challenges: list[ReproductionChallenge] = field(default_factory=list)
    #: (artifact_digest, task_id, reason) for pairs this validator could not
    #: evaluate. Distinct from a miner scoring zero: no score was produced.
    evaluation_errors: list[tuple[str, str, str]] = field(default_factory=list)
    #: task_id -> the patch that scored best, for the public audit bundle.
    accepted_patches: dict[str, str] = field(default_factory=dict)

    @property
    def round_was_noisy(self) -> bool:
        """Any voided task means no crown change this round."""
        return bool(self.voided_tasks)


@dataclass(slots=True)
class RoundRunner:
    """Executes one round's verification work on this validator."""

    ctx: EvaluationContext
    workdir: Path
    #: Only the top candidates by Tier A reach the calibrated wall-clock host.
    tier_b_top_n: int = 100

    def verify(
        self,
        plan: RoundPlan,
        patches: list[ProducedPatch],
        *,
        sealed_cases: dict[str, list[DifferentialCase]] | None = None,
        round_number: int = 0,
    ) -> RoundResult:
        """Independently verify every published patch.

        This is the part that must be done by every validator, and it is the cheap
        part. Nothing here re-executes an agent.
        """
        result = RoundResult(round_number=round_number, plan=plan)
        evaluator = Evaluator(ctx=self.ctx)
        by_task = {t.task.task_id: t for t in plan.tasks}

        cases_by_task, unpreparable = self._prepare_cases(plan, sealed_cases or {})
        # A task whose hidden inputs could not be produced cannot be
        # differentially tested. Void it rather than letting every miner fail the
        # differential gate for a problem on this side of the fence.
        for task_id, reason in unpreparable.items():
            logger.error("task %s voided before evaluation: %s", task_id[:19], reason)
            result.voided_tasks[task_id] = reason

        best_by_task: dict[str, tuple[float, str]] = {}

        for produced in patches:
            selected = by_task.get(produced.task_id)
            if selected is None:
                logger.warning("patch references unknown task %s", produced.task_id)
                continue
            if produced.task_id in result.voided_tasks:
                continue

            # Verify the published digest before spending anything on it.
            actual = ProducedPatch.digest_of(produced.patch)
            if actual != produced.patch_digest:
                result.challenges.append(
                    ReproductionChallenge(
                        producer_uid=produced.producer_uid,
                        artifact_digest=produced.artifact_digest,
                        task_id=produced.task_id,
                        published_digest=produced.patch_digest,
                        reproduced_digest=actual,
                    )
                )
                continue

            candidate = CandidatePatch(
                artifact_digest=produced.artifact_digest,
                patch=produced.patch,
                patch_digest=produced.patch_digest,
                producer_uid=produced.producer_uid,
            )

            try:
                artifact = evaluator.evaluate(
                    candidate,
                    selected,
                    cases=cases_by_task.get(produced.task_id, []),
                )
            except TaskVoided as exc:
                # The failure belongs to the task, not to this miner. Drop it for
                # everyone rather than zeroing whoever happened to be evaluated
                # when the instability surfaced.
                logger.warning("task %s voided: %s", produced.task_id[:19], exc)
                result.voided_tasks[produced.task_id] = str(exc)
                result.score_artifacts = [
                    a for a in result.score_artifacts if a.task_id != produced.task_id
                ]
                continue
            except Exception as exc:  # noqa: BLE001 - one bad patch must not end the round
                # The evaluator itself failed. This validator did not measure
                # anything, so it must not emit a score for this pair: a zero
                # here would punish the miner for a fault on this side. Marked
                # voided so aggregation excludes it, and recorded so a validator
                # crashing on every pair is visible rather than looking like a
                # round where nobody improved anything.
                logger.exception(
                    "evaluation crashed for %s on %s; emitting no score for this pair",
                    produced.artifact_digest[:19],
                    produced.task_id[:19],
                )
                result.evaluation_errors.append(
                    (produced.artifact_digest, produced.task_id, str(exc)[:300])
                )
                result.score_artifacts.append(
                    ScoreArtifact(
                        artifact_digest=produced.artifact_digest,
                        task_id=produced.task_id,
                        toolchain_digest=selected.task.build.toolchain_digest,
                        corpus_snapshot=self.ctx.corpus_snapshot,
                        verifier_hotkey=self.ctx.verifier_hotkey,
                        score=0.0,
                        voided=True,
                        void_reason=f"evaluation error: {exc}"[:300],
                    )
                )
                continue

            result.score_artifacts.append(artifact)

            if artifact.score > 0:
                prior = best_by_task.get(produced.task_id)
                if prior is None or artifact.score > prior[0]:
                    best_by_task[produced.task_id] = (artifact.score, produced.patch)

        result.accepted_patches = {
            task_id: patch
            for task_id, (_score, patch) in best_by_task.items()
            if task_id not in result.voided_tasks
        }
        return result

    def _prepare_cases(
        self, plan: RoundPlan, sealed: dict[str, list[DifferentialCase]]
    ) -> tuple[dict[str, list[DifferentialCase]], dict[str, str]]:
        """Assemble the hidden differential inputs for each task.

        Sealed material takes precedence; a package with a procedural generator
        falls back to generating from the round seed, which is equally unknowable
        in advance because the seed comes from the block hash.

        Returns ``(cases_by_task, unpreparable)``. A task lands in
        ``unpreparable`` when it cannot supply hidden inputs at all — no
        generator, no sealed corpus, or a generator that failed. Returning those
        as empty case lists instead would make every miner fail the differential
        gate for a problem that is not theirs.
        """
        out: dict[str, list[DifferentialCase]] = {}
        unpreparable: dict[str, str] = {}

        for selected in plan.tasks:
            task_id = selected.task.task_id

            if task_id in sealed:
                cases = sealed[task_id]
                if cases:
                    out[task_id] = cases
                else:
                    unpreparable[task_id] = "sealed differential corpus is empty"
                continue

            generator = selected.package.package.input_generator
            if not generator:
                unpreparable[task_id] = (
                    f"package {selected.package_id} declares neither a sealed corpus "
                    "nor an input generator, so it cannot be differentially tested"
                )
                continue

            try:
                cases = generate_cases(
                    generator,
                    seed=selected.task.seed,
                    count=200,
                    workdir=self.workdir / "cases" / task_id.removeprefix("sha256:")[:16],
                    package_root=selected.package.root,
                )
            except Exception as exc:  # noqa: BLE001 - reported, never absorbed
                unpreparable[task_id] = f"input generation failed: {exc}"
                continue

            if not cases:
                unpreparable[task_id] = "input generator produced no cases"
            else:
                out[task_id] = cases

        return out, unpreparable


def build_aggregator(plan: RoundPlan, result: RoundResult) -> RoundAggregator:
    """Fold a round's artifacts into per-artifact standings."""
    aggregator = RoundAggregator(spec_digest=SPEC.digest())
    for selected in plan.tasks:
        aggregator.register_task(
            task_id=selected.task.task_id,
            package_id=selected.package_id,
            family=str(selected.package.package.family),
            hidden=selected.hidden,
        )
    for artifact in result.score_artifacts:
        aggregator.add(artifact)
    for task_id in result.voided_tasks:
        aggregator.void_task(task_id)
    return aggregator


def select_tier_b_candidates(
    artifacts: list[ScoreArtifact], *, top_n: int
) -> list[ScoreArtifact]:
    """Order by Tier A and take the top N.

    Tier B runs on one dedicated bare-metal host per validator, so it is the
    scarcest resource in the system. Spending it on candidates that cannot reach
    the champion's lower confidence bound is wasted host time.
    """
    ranked = sorted(
        (a for a in artifacts if a.tier_a is not None),
        key=lambda a: -a.tier_a.speedup_lcb,  # type: ignore[union-attr]
    )
    return ranked[:top_n]


def selected_task_for(plan: RoundPlan, task_id: str) -> SelectedTask | None:
    for t in plan.tasks:
        if t.task.task_id == task_id:
            return t
    return None
