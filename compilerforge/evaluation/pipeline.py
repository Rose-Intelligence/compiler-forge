"""The authoritative evaluation protocol, end to end.

Seven stages. Every stage before measurement is a hard gate: failure produces zero
for the task, not a reduced score. The ordering is also a cost-control device —
the cheap gates run first and eliminate most candidates before any expensive
measurement begins — roughly a 1,000 -> 400 -> 100 funnel over a full round.

    0  task commitment and selection      precondition
    1  baseline reproduction              gate (void for everyone if unstable)
    2  build and interface conformance    gate
    3  semantic equivalence               gate
    4  fuzzing and sanitizers             gate
    5a deterministic cost (Tier A)        consensus score
    5b wall-clock (Tier B)                reporting and sign-agreement gate
    6  hidden generalisation              score modifier

Everything the validator writes down about a candidate is a measurement it made
itself. Nothing the agent claims is scored.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.corpus.package import LoadedPackage
from compilerforge.evaluation.baseline import Baseline, BaselineBuilder, BaselineUnstable
from compilerforge.evaluation.build import (
    Builder,
    Workspace,
    apply_patch,
    check_api_abi,
    check_immutable_tree,
    check_patch_hygiene,
    check_test_inventory,
    run_public_tests,
)
from compilerforge.evaluation.differential import (
    DifferentialCase,
    DifferentialRunner,
)
from compilerforge.evaluation.measurement import (
    MeasurementError,
    TierARunner,
    TierBRunner,
    check_sign_agreement,
)
from compilerforge.evaluation.safety import FuzzRunner, SanitizerRunner, SecondOptLevelCheck
from compilerforge.evaluation.selection import SelectedTask
from compilerforge.evaluation.statistics import Distribution
from compilerforge.protocol.score import (
    GateName,
    GateResult,
    ReferenceResult,
    ScoreArtifact,
    TierAResult,
    TierBResult,
)
from compilerforge.scoring.capture import compute_capture, score_components
from compilerforge.spec import SPEC

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EvaluationContext:
    """Validator-owned tooling, assembled once and reused across a round."""

    workdir: Path
    builder: Builder
    tier_a: TierARunner
    tier_b: TierBRunner | None
    baselines: BaselineBuilder
    corpus_snapshot: str
    verifier_hotkey: str
    #: Tier B needs a calibrated bare-metal host. A validator without one still
    #: participates in consensus — Tier A is the consensus tier — it just does not
    #: contribute wall-clock evidence.
    tier_b_available: bool = True
    fuzz_seconds: int = SPEC.safety.fuzz_seconds

    def sanitizers(self) -> SanitizerRunner:
        return SanitizerRunner(builder=self.builder)

    def fuzzer(self) -> FuzzRunner:
        return FuzzRunner(builder=self.builder, duration_seconds=self.fuzz_seconds)

    def second_opt(self) -> SecondOptLevelCheck:
        return SecondOptLevelCheck(builder=self.builder)


@dataclass(slots=True)
class CandidatePatch:
    """One patch to evaluate, and who produced it.

    Production happens once per (artifact, task) by a seeded producer subset; every
    validator then independently verifies *this* patch. Consensus is
    formed over the measurement, which is where independence actually matters.
    """

    artifact_digest: str
    patch: str
    patch_digest: str
    producer_uid: int | None = None
    #: Informational only, never scored.
    claimed_speedup: float | None = None


class TaskVoided(RuntimeError):
    """The task produced no score for anyone. Distinct from a candidate failing."""


class CandidateUnmeasurable(RuntimeError):
    """This patch could not be measured. A zero for its author, not a void."""


@dataclass(slots=True)
class Evaluator:
    """Runs the gate sequence for one (candidate, task) pair."""

    ctx: EvaluationContext
    _gates: list[GateResult] = field(default_factory=list, init=False)

    def evaluate(
        self,
        candidate: CandidatePatch,
        selected: SelectedTask,
        *,
        cases: list[DifferentialCase],
        baseline: Baseline | None = None,
        score: bool = True,
    ) -> ScoreArtifact:
        """Evaluate one candidate and return a signed-ready score artifact.

        Raises ``TaskVoided`` when the failure belongs to the task rather than to
        the candidate — an unstable baseline or persistent tier divergence — so the
        caller can drop the task for the whole round instead of zeroing one miner.

        With ``score=False`` the gate sequence and both measurement tiers run
        exactly as they do on a validator, but capture and the component score are
        not computed. That combination is not a consensus result and must never be
        weighted — it exists because a package can be verified and measured
        without an expert reference patch to normalise against, and someone who
        wants to know whether their own patch is correct and faster should not
        have to author one first.
        """
        self._gates = []
        task = selected.task
        package = selected.package

        artifact = ScoreArtifact(
            artifact_digest=candidate.artifact_digest,
            task_id=task.task_id,
            toolchain_digest=task.build.toolchain_digest,
            corpus_snapshot=self.ctx.corpus_snapshot,
            producer_uid=candidate.producer_uid,
            verifier_hotkey=self.ctx.verifier_hotkey,
            patch_digest=candidate.patch_digest,
        )

        run_root = self.ctx.workdir / f"eval-{candidate.artifact_digest[7:19]}-{int(time.time()*1000)}"
        run_root.mkdir(parents=True, exist_ok=True)
        base_ws: Workspace | None = None
        cand_ws: Workspace | None = None

        try:
            # -- Stage 1: baseline -------------------------------------------
            base_ws = Workspace.create(package.repo_dir, run_root / "baseline")
            if baseline is None:
                try:
                    baseline, stability = self.ctx.baselines.reproduce(base_ws, task)
                except BaselineUnstable as exc:
                    raise TaskVoided(str(exc)) from exc
                self._record(stability)
                if not stability.passed:
                    raise TaskVoided(stability.detail)
            else:
                self._record(
                    GateResult(
                        name=GateName.BASELINE_STABLE,
                        passed=True,
                        detail="reused round baseline",
                    )
                )

            # -- Stage 2: patch, build, interface ----------------------------
            self._record(check_patch_hygiene(candidate.patch, task))
            if self._failed():
                return self._finish(artifact)

            cand_ws = Workspace.create(package.repo_dir, run_root / "candidate")
            self._record(apply_patch(cand_ws, candidate.patch))
            if self._failed():
                return self._finish(artifact)

            # Verified against the resulting tree, not the diff, and before any
            # build or measurement runs against it.
            self._record(check_immutable_tree(cand_ws, task))
            if self._failed():
                return self._finish(artifact)

            build_started = time.monotonic()
            build = self.ctx.builder.build(cand_ws, task, opt_level="-O2")
            self._record(
                GateResult(
                    name=GateName.BUILD,
                    passed=build.ok,
                    detail=(
                        "built with the pinned toolchain"
                        if build.ok
                        else build.log_tail[-400:]
                    ),
                    seconds=time.monotonic() - build_started,
                )
            )
            if self._failed():
                return self._finish(artifact)

            self._record(check_test_inventory(cand_ws, task, package.package.test_inventory_globs))
            self._record(check_api_abi(base_ws, cand_ws, task))
            if self._failed():
                return self._finish(artifact)

            self._record(run_public_tests(cand_ws, task))
            if self._failed():
                return self._finish(artifact)

            # -- Stage 3: semantic equivalence -------------------------------
            differ = DifferentialRunner(
                run_command=task.tests.differential_command or task.benchmark.command
            )
            self._record(differ.run(base_ws, cand_ws, task, cases).as_gate())
            if self._failed():
                return self._finish(artifact)

            # -- Stage 4: fuzzing and sanitizers -----------------------------
            self._run_safety_gates(cand_ws, task, package, baseline)
            if self._failed():
                return self._finish(artifact)

            # -- Stage 5a: deterministic cost (consensus) --------------------
            # Two very different failures live here and must not be conflated.
            # If the *baseline* cannot be measured the task is broken for
            # everyone. If the *candidate* cannot be measured that is this
            # miner's patch, and voiding the task would let anyone kill any task
            # at will — and, since a voided round cannot change the crown, let an
            # incumbent defend itself indefinitely.
            try:
                tier_a = self._measure_tier_a(base_ws, cand_ws, task, baseline)
            except CandidateUnmeasurable as exc:
                self._record(
                    GateResult(
                        name=GateName.CANDIDATE_MEASURABLE,
                        passed=False,
                        detail=str(exc)[:300],
                    )
                )
                return self._finish(artifact)
            except MeasurementError as exc:
                # The baseline side. A measurement we cannot trust is not a low
                # score; it is no score, for anyone.
                raise TaskVoided(f"Tier A baseline measurement failed: {exc}") from exc

            self._record(
                GateResult(
                    name=GateName.CANDIDATE_MEASURABLE,
                    passed=True,
                    detail="candidate measured deterministically",
                )
            )
            artifact.tier_a = tier_a

            # -- Stage 5b: wall-clock (reporting and sign agreement) ---------
            tier_b = self._measure_tier_b(base_ws, cand_ws, task, tier_a, baseline, build)
            artifact.tier_b = tier_b

            # -- Scoring -----------------------------------------------------
            if not score:
                # Verification only: everything above is what a validator does,
                # and everything below turns it into a number consensus can use.
                return self._finish(artifact)

            s_ref = package.reference_speedup(selected.profile)
            if s_ref is None:
                raise TaskVoided(
                    f"package {package.package.package_id} declares no measured reference "
                    f"speedup for profile {selected.profile.name!r}; capture has nothing to "
                    "normalise against"
                )

            capture = compute_capture(s_lcb=tier_a.speedup_lcb, s_ref=s_ref)
            artifact.reference = ReferenceResult(s_ref=s_ref, capture=capture)
            artifact.score = score_components(
                capture=capture,
                tier_a=tier_a,
                tier_b=tier_b,
                hidden=selected.hidden,
            )
            artifact.honest_null = (
                capture == 0.0 and tier_a.speedup_lcb < SPEC.capture.min_improvement_speedup
            )
            return self._finish(artifact)

        finally:
            for ws in (base_ws, cand_ws):
                if ws is not None:
                    ws.cleanup()

    # -- stage helpers ---------------------------------------------------

    def _run_safety_gates(
        self,
        cand_ws: Workspace,
        task,
        package: LoadedPackage,
        baseline: Baseline | None,
    ) -> None:
        sanitizers = self.ctx.sanitizers()

        if SPEC.safety.asan_required:
            self._record(sanitizers.run(cand_ws, task, "address").as_gate(GateName.ASAN))
            if self._failed():
                return
        if SPEC.safety.ubsan_required:
            self._record(sanitizers.run(cand_ws, task, "undefined").as_gate(GateName.UBSAN))
            if self._failed():
                return

        if SPEC.safety.fuzz_required and package.package.fuzz_targets:
            fuzzer = self.ctx.fuzzer()
            for target in package.package.fuzz_targets:
                self._record(fuzzer.run(cand_ws, task, target).as_gate())
                if self._failed():
                    return

        if SPEC.safety.cross_check_second_opt_level and baseline is not None:
            # Rebuilding mutates the tree, so this runs last among the safety
            # gates and the -O2 build is restored before measurement.
            import subprocess

            reference = subprocess.run(  # noqa: S603
                ["/bin/sh", "-c", task.benchmark.command],
                cwd=cand_ws.root,
                capture_output=True,
                timeout=task.benchmark.max_wall_seconds,
                check=False,
            ).stdout
            self._record(
                self.ctx.second_opt().run(cand_ws, task, reference_output=reference)
            )
            self.ctx.builder.build(cand_ws, task, opt_level="-O2")

    def _measure_tier_a(
        self, base_ws: Workspace, cand_ws: Workspace, task, baseline: Baseline | None
    ) -> TierAResult:
        """Measure both sides, attributing any failure to the side that caused it.

        Raises ``CandidateUnmeasurable`` for the candidate and ``MeasurementError``
        for the baseline, so the caller can zero one miner rather than void a task
        for all of them.
        """
        # Baseline first. Anything wrong here belongs to the task.
        base_counters = (
            list(baseline.tier_a_counters)
            if baseline is not None
            else self.ctx.tier_a.measure(
                task.benchmark.command,
                cwd=base_ws.root,
                instrument_at_start=task.benchmark.instrument_at_start,
            )
        )

        try:
            cand_counters = self.ctx.tier_a.measure(
            task.benchmark.command,
            cwd=cand_ws.root,
            instrument_at_start=task.benchmark.instrument_at_start,
        )
        except MeasurementError as exc:
            raise CandidateUnmeasurable(
                f"the patched build could not be measured: {exc}"
            ) from exc

        # compare() also rejects a non-deterministic side. Check the candidate's
        # own spread first so its non-determinism is attributed to the patch.
        spread = Distribution(tuple(float(c.ir) for c in cand_counters)).relative_spread
        if spread > SPEC.stability.max_tier_a_relative_spread:
            raise CandidateUnmeasurable(
                f"the patched build is not deterministic: Tier A varies by "
                f"{spread:.6%} across repeats, above the "
                f"{SPEC.stability.max_tier_a_relative_spread:.6%} tolerance"
            )

        return self.ctx.tier_a.compare(base_counters, cand_counters)

    def _measure_tier_b(
        self,
        base_ws: Workspace,
        cand_ws: Workspace,
        task,
        tier_a: TierAResult,
        baseline: Baseline | None,
        build,
    ) -> TierBResult | None:
        """Wall-clock plus the sign-agreement gate.

        Only the top candidates reach Tier B in production; the
        caller decides who by ordering on Tier A. A validator without a calibrated
        host returns ``None`` here and still contributes a full consensus weight.
        """
        if not self.ctx.tier_b_available or self.ctx.tier_b is None:
            return None

        from compilerforge.evaluation.baseline import measure_peak_rss

        # Re-measure a confident sign disagreement before acting on it: 2.7%
        # wall-clock CV means a single flipped sign is frequently noise. The
        # gate's power is to *contradict* — Tier A says faster, a calibrated host
        # says confidently slower — and only *persistent* divergence across the
        # allowed reruns is the adversarial signal (an instruction-count win that
        # destroys memory-level parallelism). That voids the task rather than
        # letting a fake speedup into the weight vector. The check is written to
        # return ``should_void`` only on the final attempt, so the loop must
        # actually reach it — a single call at attempt 0 never can.
        base_dist = cand_dist = None
        drift = False
        agreement = None
        for attempt in range(SPEC.measurement.sign_agreement_reruns + 1):
            try:
                base_dist, cand_dist, drift = self.ctx.tier_b.measure_pair(
                    task.benchmark.command,
                    task.benchmark.command,
                    baseline_cwd=base_ws.root,
                    candidate_cwd=cand_ws.root,
                    seed=int(task.seed, 16) & 0xFFFFFFFF,
                )
            except MeasurementError as exc:
                logger.warning("Tier B unavailable for %s: %s", task.task_id, exc)
                return None

            if drift:
                # A drifted reading can trust neither the sign check nor the
                # reported wall-clock, so it is discarded rather than corrected.
                # Tier A is unaffected and still scores. Drift is host noise, not
                # a candidate defect, so it never voids.
                logger.warning(
                    "thermal drift on the calibrated host; Tier B discarded for %s", task.task_id
                )
                return None

            agreement = check_sign_agreement(tier_a, base_dist, cand_dist, attempt=attempt)
            if agreement.should_void:
                raise TaskVoided(agreement.detail)
            if not agreement.should_rerun:
                break
            logger.info(
                "Tier B sign disagreement on %s; re-measuring — %s", task.task_id, agreement.detail
            )

        result = self.ctx.tier_b.compare(
            base_dist,
            cand_dist,
            host_drift=drift,
            peak_rss_baseline=baseline.peak_rss_bytes if baseline else None,
            peak_rss_candidate=measure_peak_rss(task.benchmark.command, cand_ws.root),
            compile_seconds_baseline=baseline.compile_seconds if baseline else None,
            compile_seconds_candidate=build.compile_seconds,
        )
        return result.model_copy(update={"sign_agreement": agreement.agreed})

    # -- bookkeeping -----------------------------------------------------

    def _record(self, gate: GateResult) -> None:
        self._gates.append(gate)
        if not gate.passed:
            logger.info("gate %s failed: %s", gate.name, gate.detail)

    def _failed(self) -> bool:
        return any(not g.passed for g in self._gates)

    def _finish(self, artifact: ScoreArtifact) -> ScoreArtifact:
        artifact.gates = list(self._gates)
        if not artifact.all_gates_passed():
            artifact.score = 0.0
            artifact.honest_null = False
        return artifact
