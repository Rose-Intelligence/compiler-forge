"""Stage 4 — fuzzing and safety instrumentation.

These gates exist because of one specific failure mode:

    A candidate that introduces undefined behaviour is not fast; it is unsound
    and merely appears fast under one compiler configuration.

UBSan and ASan are mandatory in V1. ThreadSanitizer is reserved for concurrency
tracks — it is too expensive as a universal gate. Rebuilding at a second
optimization level is a cheap additional detector for exactly this class of fake
win: code whose speed depends on the compiler exploiting UB will usually change
behaviour when the optimizer's assumptions change.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from compilerforge.evaluation.build import Builder, BuildResult, Workspace
from compilerforge.protocol.score import GateName, GateResult
from compilerforge.protocol.task import Task
from compilerforge.spec import SPEC

#: Sanitizer runtimes announce themselves in a stable, greppable way.
_ASAN_RE = re.compile(r"ERROR: AddressSanitizer: (\S+)")
_LSAN_RE = re.compile(r"ERROR: LeakSanitizer: (\S+)")
_UBSAN_RE = re.compile(r"runtime error: (.+)")
_TSAN_RE = re.compile(r"WARNING: ThreadSanitizer: (\S+)")


@dataclass(frozen=True, slots=True)
class SanitizerFinding:
    sanitizer: str
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class SanitizerReport:
    sanitizer: str
    findings: tuple[SanitizerFinding, ...]
    seconds: float
    build_failed: bool = False
    build_log: str = ""

    @property
    def clean(self) -> bool:
        return not self.build_failed and not self.findings

    def as_gate(self, name: GateName) -> GateResult:
        if self.build_failed:
            detail = f"{self.sanitizer} build failed: {self.build_log[-300:]}"
        elif self.findings:
            detail = "; ".join(f"{f.kind}: {f.detail}" for f in self.findings[:3])
        else:
            detail = "no finding"
        return GateResult(name=name, passed=self.clean, detail=detail, seconds=self.seconds)


def _scan(output: str, sanitizer: str) -> list[SanitizerFinding]:
    findings: list[SanitizerFinding] = []
    match sanitizer:
        case "address":
            for m in _ASAN_RE.finditer(output):
                findings.append(SanitizerFinding("address", m.group(1), _context(output, m.start())))
            for m in _LSAN_RE.finditer(output):
                findings.append(SanitizerFinding("leak", m.group(1), _context(output, m.start())))
        case "undefined":
            for m in _UBSAN_RE.finditer(output):
                findings.append(SanitizerFinding("undefined", "runtime error", m.group(1).strip()))
        case "thread":
            for m in _TSAN_RE.finditer(output):
                findings.append(SanitizerFinding("thread", m.group(1), _context(output, m.start())))
    return findings


def _context(output: str, position: int, span: int = 200) -> str:
    return output[position : position + span].replace("\n", " ").strip()


@dataclass(slots=True)
class SanitizerRunner:
    """Build with instrumentation, then run the benchmark and the test suite."""

    builder: Builder
    timeout_seconds: int = 3600

    def run(
        self,
        workspace: Workspace,
        task: Task,
        sanitizer: str,
        *,
        commands: tuple[str, ...] = (),
    ) -> SanitizerReport:
        started = time.monotonic()

        build: BuildResult = self.builder.build(
            workspace, task, opt_level="-O1", sanitizers=(sanitizer,)
        )
        if not build.ok:
            return SanitizerReport(
                sanitizer=sanitizer,
                findings=(),
                seconds=time.monotonic() - started,
                build_failed=True,
                build_log=build.log_tail,
            )

        # halt_on_error=0 so one finding does not mask the rest; the gate fails
        # either way, but the audit bundle is more useful with all of them.
        env = {
            "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=0:abort_on_error=0:detect_stack_use_after_return=1",
            "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=0:report_error_type=1",
            "TSAN_OPTIONS": "halt_on_error=0",
        }

        to_run = commands or (task.tests.public_command, task.benchmark.command)
        findings: list[SanitizerFinding] = []
        for command in to_run:
            proc = subprocess.run(  # noqa: S603
                ["/bin/sh", "-c", command],
                cwd=workspace.root,
                env={**env},
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            findings.extend(_scan(proc.stdout + proc.stderr, sanitizer))

        return SanitizerReport(
            sanitizer=sanitizer, findings=tuple(findings), seconds=time.monotonic() - started
        )


@dataclass(frozen=True, slots=True)
class FuzzReport:
    target: str
    seconds: float
    executions: int
    crashes: int
    divergences: int
    detail: str = ""
    #: False when a declared fuzz target could not be built. That is a gate
    #: failure, not a pass: a patch that breaks the harness's compilation must not
    #: skip fuzzing rather than fail it.
    built: bool = True

    @property
    def clean(self) -> bool:
        return self.built and self.crashes == 0 and self.divergences == 0

    def as_gate(self) -> GateResult:
        detail = self.detail or (
            f"{self.executions} executions, no crash or divergence"
            if self.clean
            else f"{self.crashes} crashes, {self.divergences} divergences"
        )
        return GateResult(
            name=GateName.FUZZ,
            passed=self.clean,
            detail=detail,
            seconds=self.seconds,
        )


@dataclass(slots=True)
class FuzzRunner:
    """Coverage-guided fuzzing with libFuzzer.

    libFuzzer is in-process and integrates directly with the Clang toolchain V1
    already pins, which is why it is the V1 choice over AFL++. The fuzz target
    belongs to the task package and is mounted read-only — a miner-supplied fuzz
    target would be a fuzz target that finds nothing.
    """

    builder: Builder
    duration_seconds: int = SPEC.safety.fuzz_seconds

    def run(
        self,
        workspace: Workspace,
        task: Task,
        target: str,
        *,
        corpus_dir: Path | None = None,
    ) -> FuzzReport:
        started = time.monotonic()

        target_path = workspace.root / target
        if not target_path.exists():
            # A declared target that is not on disk means the patch broke its
            # build. Fail the gate rather than skip fuzzing.
            return FuzzReport(
                target=target,
                seconds=time.monotonic() - started,
                executions=0,
                crashes=0,
                divergences=0,
                built=False,
                detail=f"declared fuzz target {target} was not built by the patch",
            )

        artifacts = workspace.root / ".cf-fuzz"
        artifacts.mkdir(exist_ok=True)

        args = [
            str(target_path),
            f"-max_total_time={self.duration_seconds}",
            f"-artifact_prefix={artifacts}/",
            "-print_final_stats=1",
            "-rss_limit_mb=2048",
            "-timeout=25",
        ]
        if corpus_dir and corpus_dir.exists():
            args.append(str(corpus_dir))

        proc = subprocess.run(  # noqa: S603
            args,
            cwd=workspace.root,
            capture_output=True,
            text=True,
            timeout=self.duration_seconds + 300,
            check=False,
        )
        output = proc.stdout + proc.stderr
        executions = _parse_executions(output)
        crash_files = [p for p in artifacts.iterdir() if p.is_file()] if artifacts.exists() else []

        return FuzzReport(
            target=target,
            seconds=time.monotonic() - started,
            executions=executions,
            crashes=len(crash_files),
            divergences=0,
            detail=output[-300:] if crash_files else "",
        )


_EXEC_RE = re.compile(r"stat::number_of_executed_units:\s*(\d+)")


def _parse_executions(output: str) -> int:
    m = _EXEC_RE.search(output)
    return int(m.group(1)) if m else 0


@dataclass(slots=True)
class SecondOptLevelCheck:
    """Rebuild the patched source at a different optimization level.

    Cheap, and it catches the specific case where a "speedup" only exists because
    the optimizer was allowed to assume something the code violates. If the
    program's observable behaviour changes with the optimization level, the patch
    is unsound regardless of what the benchmark says.
    """

    builder: Builder

    def run(
        self,
        workspace: Workspace,
        task: Task,
        *,
        reference_output: bytes,
        opt_level: str | None = None,
    ) -> GateResult:
        started = time.monotonic()
        level = opt_level or SPEC.safety.second_opt_level

        build = self.builder.build(workspace, task, opt_level=level)
        if not build.ok:
            return GateResult(
                name=GateName.SECOND_OPT_LEVEL,
                passed=False,
                detail=f"rebuild at {level} failed: {build.log_tail[-300:]}",
                seconds=time.monotonic() - started,
            )

        proc = subprocess.run(  # noqa: S603
            ["/bin/sh", "-c", task.benchmark.command],
            cwd=workspace.root,
            capture_output=True,
            timeout=task.benchmark.max_wall_seconds,
            check=False,
        )
        matches = proc.stdout == reference_output
        return GateResult(
            name=GateName.SECOND_OPT_LEVEL,
            passed=matches,
            detail=(
                f"behaviour identical at {level}"
                if matches
                else f"output changed when rebuilt at {level} — likely undefined behaviour"
            ),
            seconds=time.monotonic() - started,
        )
