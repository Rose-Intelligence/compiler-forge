"""The local evaluator miners develop against.

Shipping this is the difference between attracting miners and attracting
complaints. It runs the same gate sequence a validator runs, in the same order,
using the same code — so "it passed locally but scored zero on chain" should mean
a real environment difference, not a surprise about the rules.

Two honest differences from an authoritative run, both stated rather than hidden:

* **Hidden inputs.** A validator's differential corpus is sealed until after the
  round. Locally you get inputs generated from a seed you chose, which is weaker
  evidence than a validator will demand.
* **Isolation.** Local runs execute your agent as an ordinary subprocess. The
  validator runs it with no network, a read-only root filesystem, dropped
  capabilities and a hard resource cap. Code that works locally and reaches for
  the network will fail there.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.corpus.package import LoadedPackage
from compilerforge.evaluation.baseline import BaselineBuilder, BaselineUnstable
from compilerforge.evaluation.build import Builder, toolchain_digest
from compilerforge.evaluation.differential import DifferentialCase, generate_cases
from compilerforge.evaluation.measurement import TierARunner, TierBRunner
from compilerforge.evaluation.pipeline import (
    CandidatePatch,
    EvaluationContext,
    Evaluator,
    TaskVoided,
)
from compilerforge.evaluation.selection import SelectedTask
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.sandbox.runner import ArtifactRun, local_run
from compilerforge.spec import SPEC


@dataclass(slots=True)
class LocalResult:
    """What one local evaluation produced."""

    run: ArtifactRun | None
    score: ScoreArtifact | None
    voided_reason: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.score is not None and self.score.all_gates_passed()

    def summary(self) -> str:
        if self.voided_reason:
            return f"TASK VOIDED: {self.voided_reason}"
        if self.score is None:
            return "no score produced"
        failed = self.score.failed_gate()
        if failed is not None:
            detail = next(g.detail for g in self.score.gates if g.name == failed)
            return f"FAILED at {failed}: {detail}"
        if self.score.honest_null:
            return "PASSED every gate, no improvement — an honest null result"
        capture = self.score.reference.capture if self.score.reference else 0.0
        speedup = self.score.tier_a.deterministic_speedup if self.score.tier_a else 1.0
        return (
            f"PASSED — {speedup:.4f}x deterministic, capture {capture:.3f}, "
            f"score {self.score.score:.4f}"
        )


@dataclass(slots=True)
class LocalEvaluator:
    """Reproduces the validator's gate sequence on a developer machine."""

    workdir: Path
    corpus_snapshot: str = "local"
    tier_b_enabled: bool = False
    fuzz_seconds: int = 60
    _builder: Builder = field(default_factory=Builder, init=False)

    def context(self) -> EvaluationContext:
        tier_a = TierARunner()
        return EvaluationContext(
            workdir=self.workdir / "eval",
            builder=self._builder,
            tier_a=tier_a,
            tier_b=(
                TierBRunner(require_calibration=False) if self.tier_b_enabled else None
            ),
            baselines=BaselineBuilder(
                builder=self._builder,
                tier_a=tier_a,
                cache_dir=self.workdir / "baselines",
            ),
            corpus_snapshot=self.corpus_snapshot,
            verifier_hotkey="local",
            tier_b_available=self.tier_b_enabled,
            fuzz_seconds=self.fuzz_seconds,
        )

    def build_task(
        self, package: LoadedPackage, *, seed: str, profile_name: str | None = None
    ) -> SelectedTask:
        """Build a task instance for local development.

        The seed is yours to choose. On chain it comes from a block hash you cannot
        predict, so passing several different seeds locally is the closest you can
        get to the real thing.
        """
        profiles = package.package.workload_profiles
        profile = profiles[0]
        if profile_name:
            matches = [p for p in profiles if p.name == profile_name]
            if not matches:
                raise ValueError(
                    f"unknown profile {profile_name!r}; available: "
                    + ", ".join(p.name for p in profiles)
                )
            profile = matches[0]

        task = package.build_task(
            task_id="sha256:" + "0" * 64,
            seed=seed,
            toolchain_digest=toolchain_digest(),
            profile=profile,
        )
        return SelectedTask(task=task, package=package, profile=profile, hidden=False)

    def run_agent(
        self, entrypoint: list[str], selected: SelectedTask
    ) -> ArtifactRun:
        """Execute an agent as a local subprocess against the task contract."""
        return local_run(
            entrypoint,
            selected.task,
            selected.package.repo_dir,
            self.workdir / "agent",
        )

    def evaluate_patch(
        self,
        patch: str,
        selected: SelectedTask,
        *,
        cases: list[DifferentialCase] | None = None,
        artifact_digest: str = "sha256:" + "0" * 64,
    ) -> LocalResult:
        """Run the full gate sequence against an existing patch."""
        started = time.monotonic()

        if cases is None:
            cases = self._generate_cases(selected)

        candidate = CandidatePatch(
            artifact_digest=artifact_digest,
            patch=patch,
            patch_digest="sha256:" + "0" * 64,
        )

        evaluator = Evaluator(ctx=self.context())
        try:
            score = evaluator.evaluate(candidate, selected, cases=cases)
        except (TaskVoided, BaselineUnstable) as exc:
            return LocalResult(
                run=None,
                score=None,
                voided_reason=str(exc),
                seconds=time.monotonic() - started,
            )

        return LocalResult(run=None, score=score, seconds=time.monotonic() - started)

    def evaluate_agent(
        self, entrypoint: list[str], selected: SelectedTask
    ) -> LocalResult:
        """Run an agent, then evaluate whatever it produced."""
        started = time.monotonic()
        run = self.run_agent(entrypoint, selected)

        if not run.produced_patch:
            # An honest empty result is a legitimate outcome, not a failure.
            return LocalResult(
                run=run, score=None, seconds=time.monotonic() - started
            )

        result = self.evaluate_patch(run.patch, selected)
        result.run = run
        result.seconds = time.monotonic() - started
        return result

    def _generate_cases(self, selected: SelectedTask) -> list[DifferentialCase]:
        generator = selected.package.package.input_generator
        if not generator:
            return []
        return generate_cases(
            generator,
            seed=selected.task.seed,
            count=100,
            workdir=self.workdir / "cases",
            package_root=selected.package.root,
        )

    def interface_problems(self, run: ArtifactRun, selected: SelectedTask) -> list[str]:
        """Interface violations, as a checklist rather than one exception.

        A validator stops at the first violation; a developer wants all of them.
        """
        problems: list[str] = []
        task = selected.task

        if run.timed_out:
            problems.append(
                f"exceeded the {task.benchmark.max_wall_seconds}s wall-clock budget"
            )
        if run.report is None:
            problems.append(
                f"no parseable /output/report.json was written ({run.report_error})"
                if run.report_error
                else "no parseable /output/report.json was written"
            )
            return problems

        if run.report.interface_version != task.interface_version:
            problems.append(
                f"report declares interface {run.report.interface_version}, "
                f"the task requires {task.interface_version}"
            )
        if run.report.task_id != task.task_id:
            problems.append("report task_id does not match the task it was given")

        budget = task.resources.model_token_budget
        if run.report.budget_used.model_tokens > budget:
            problems.append(
                f"self-reported token use {run.report.budget_used.model_tokens} "
                f"exceeds the {budget} budget"
            )

        if run.produced_patch:
            from compilerforge.evaluation.build import parse_patch

            stats = parse_patch(run.patch)
            if stats.touched_count > SPEC.budget.max_patch_changed_files:
                problems.append(
                    f"patch touches {stats.touched_count} files, cap is "
                    f"{SPEC.budget.max_patch_changed_files}"
                )
            if stats.added_lines > SPEC.budget.max_patch_added_lines:
                problems.append(
                    f"patch adds {stats.added_lines} lines, cap is "
                    f"{SPEC.budget.max_patch_added_lines}"
                )

        return problems
