"""Stage 1 — baseline reproduction.

    If the baseline itself is not reproducible within tolerance, the task is
    void — for every miner, not just the one being evaluated. Silently scoring
    against an unstable baseline is the single easiest way to destroy consensus.

Baselines are immutable per (revision, toolchain_digest, workload) and are built
once, ever. The cache is content-addressed for the same reason the
artifacts are: two validators must be able to check they used the same baseline.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from compilerforge.evaluation.build import Builder, Workspace
from compilerforge.evaluation.measurement import (
    CallgrindCounters,
    MeasurementError,
    TierARunner,
)
from compilerforge.evaluation.statistics import Distribution
from compilerforge.protocol.score import GateName, GateResult
from compilerforge.protocol.task import Task
from compilerforge.spec import SPEC
from compilerforge.utils.logging import logger


class BaselineUnstable(RuntimeError):
    """The task is void for everyone. Never scored against."""


@dataclass(frozen=True, slots=True)
class Baseline:
    """Everything measured about the unmodified repository."""

    task_id: str
    revision: str
    toolchain_digest: str
    benchmark_command: str

    tier_a_counters: tuple[CallgrindCounters, ...]
    compile_seconds: float
    reference_stdout_digest: str
    peak_rss_bytes: int | None = None
    wall_samples: tuple[float, ...] = ()

    @property
    def instructions(self) -> int:
        import numpy as np

        return int(np.median([c.ir for c in self.tier_a_counters]))

    def cache_key(self) -> str:
        payload = "|".join(
            [self.revision, self.toolchain_digest, self.benchmark_command, self.task_id]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class BaselineBuilder:
    """Produces and caches baselines, and voids tasks whose baseline drifts."""

    builder: Builder
    tier_a: TierARunner
    cache_dir: Path
    _memory: dict[str, Baseline] = field(default_factory=dict)

    def reproduce(
        self,
        workspace: Workspace,
        task: Task,
        *,
        repeats: int | None = None,
    ) -> tuple[Baseline, GateResult]:
        """Build the unmodified repository and characterise it.

        Returns the baseline plus the stability gate. A failing gate means the
        task is void for the whole round, not that this miner scored zero.
        """
        started = time.monotonic()
        key = hashlib.sha256(
            f"{task.repository.revision}|{task.build.toolchain_digest}|"
            f"{task.benchmark.command}|{task.task_id}".encode()
        ).hexdigest()

        cached = self._load(key)
        if cached is not None:
            # The measurements are cached; the built tree is not. Later stages
            # still execute the baseline — differential testing runs it against
            # every hidden input — so the workspace must be usable even when
            # nothing needs re-measuring. Skipping this build is how a cache hit
            # turns into "exit code 127" three stages later.
            build = self.builder.build(workspace, task, opt_level="-O2")
            if not build.ok:
                raise BaselineUnstable(
                    "baseline no longer builds with the pinned toolchain, though a "
                    f"cached measurement exists: {build.log_tail[-400:]}"
                )
            return cached, GateResult(
                name=GateName.BASELINE_STABLE,
                passed=True,
                detail="reused cached baseline measurements",
                seconds=time.monotonic() - started,
            )

        build = self.builder.build(workspace, task, opt_level="-O2")
        if not build.ok:
            raise BaselineUnstable(
                f"baseline does not build with the pinned toolchain: {build.log_tail[-400:]}"
            )

        n = repeats if repeats is not None else max(
            SPEC.stability.min_baseline_repeats, SPEC.measurement.tier_a_repeats
        )
        try:
            counters = self.tier_a.measure(
                task.benchmark.command,
                cwd=workspace.root,
                repeats=n,
                instrument_at_start=task.benchmark.instrument_at_start,
            )
        except MeasurementError as exc:
            raise BaselineUnstable(f"baseline cannot be measured deterministically: {exc}") from exc

        dist = Distribution(tuple(float(c.ir) for c in counters))
        spread = dist.relative_spread
        stable = spread <= SPEC.stability.max_tier_a_relative_spread

        reference_stdout = subprocess.run(  # noqa: S603
            ["/bin/sh", "-c", task.benchmark.command],
            cwd=workspace.root,
            capture_output=True,
            timeout=task.benchmark.max_wall_seconds,
            check=False,
        ).stdout

        baseline = Baseline(
            task_id=task.task_id,
            revision=task.repository.revision,
            toolchain_digest=task.build.toolchain_digest,
            benchmark_command=task.benchmark.command,
            tier_a_counters=tuple(counters),
            compile_seconds=build.compile_seconds,
            reference_stdout_digest="sha256:" + hashlib.sha256(reference_stdout).hexdigest(),
            peak_rss_bytes=measure_peak_rss(task.benchmark.command, workspace.root),
        )

        gate = GateResult(
            name=GateName.BASELINE_STABLE,
            passed=stable,
            detail=(
                f"Tier A spread {spread:.8%} over {n} repeats"
                if stable
                else f"Tier A spread {spread:.6%} exceeds "
                f"{SPEC.stability.max_tier_a_relative_spread:.6%}; task voided for all miners"
            ),
            seconds=time.monotonic() - started,
        )

        if stable:
            self._store(key, baseline)
        return baseline, gate

    def _load(self, key: str) -> Baseline | None:
        if key in self._memory:
            return self._memory[key]
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # The cache is derived data, so a corrupt entry is recoverable by
            # rebuilding it — but it is never ignored, because a cache that keeps
            # corrupting entries is a disk problem an operator needs to see.
            logger.warning("discarding unreadable baseline cache %s: %s", path, exc)
            path.unlink(missing_ok=True)
            return None

        try:
            baseline = Baseline(
                task_id=data["task_id"],
                revision=data["revision"],
                toolchain_digest=data["toolchain_digest"],
                benchmark_command=data["benchmark_command"],
                tier_a_counters=tuple(CallgrindCounters(**c) for c in data["tier_a_counters"]),
                compile_seconds=data["compile_seconds"],
                reference_stdout_digest=data["reference_stdout_digest"],
                peak_rss_bytes=data.get("peak_rss_bytes"),
                wall_samples=tuple(data.get("wall_samples", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "baseline cache %s was written by an incompatible version (%s); "
                "rebuilding",
                path,
                exc,
            )
            path.unlink(missing_ok=True)
            return None

        self._memory[key] = baseline
        return baseline

    def _store(self, key: str, baseline: Baseline) -> None:
        self._memory[key] = baseline
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(baseline)
        payload["tier_a_counters"] = [asdict(c) for c in baseline.tier_a_counters]
        (self.cache_dir / f"{key}.json").write_text(json.dumps(payload, indent=2))


def measure_peak_rss(command: str, cwd: Path, *, timeout: int = 1800) -> int | None:
    """Peak resident set size, feeding the 15% memory component.

    Uses ``/usr/bin/time -v``, which reports the kernel's own high-water mark.
    Massif gives a far richer allocation profile and is the right tool for the
    customer-facing report; for scoring, the peak is the number that matters.
    """
    import shutil

    if not shutil.which("/usr/bin/time"):
        logger.warning(
            "/usr/bin/time is not installed, so peak memory cannot be measured. "
            "The memory component of the score will be forfeited for every "
            "candidate on this validator; install GNU time to contribute it."
        )
        return None

    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/time", "-v", "/bin/sh", "-c", command],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    for line in proc.stderr.splitlines():
        if "Maximum resident set size" in line:
            try:
                return int(line.split(":")[-1].strip()) * 1024
            except ValueError:
                logger.warning("unparseable peak RSS line from /usr/bin/time: %r", line)
                return None

    logger.debug("/usr/bin/time reported no peak RSS for %r", command)
    return None
