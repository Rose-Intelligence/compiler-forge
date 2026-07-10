"""Stage 3 — semantic equivalence on hidden inputs.

Baseline and challenger execute against the same inputs and every declared
observable is compared. The inputs are hidden: published in advance as
drand-timelocked ciphertext so the network can verify afterwards that the
validator could not have read them early and the miner certainly could not.

The permanent limit, restated everywhere this module's output is used: passing a
finite test suite is not proof of universal equivalence. What this stage produces
is evidence with a stated scope.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from compilerforge.corpus.equivalence import (
    EVIDENCE_SCOPE_NOTICE,
    ComparisonResult,
    Divergence,
    Observation,
    comparator_for,
)
from compilerforge.evaluation.build import Workspace
from compilerforge.protocol.score import GateName, GateResult
from compilerforge.protocol.task import Task


class DifferentialError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DifferentialCase:
    """One hidden input plus how to feed it to the program."""

    case_id: str
    argv: tuple[str, ...] = ()
    stdin: bytes = b""
    files: dict[str, bytes] | None = None


@dataclass(frozen=True, slots=True)
class DifferentialReport:
    cases_run: int
    cases_matched: int
    divergences: tuple[Divergence, ...]
    seconds: float
    scope_notice: str = EVIDENCE_SCOPE_NOTICE

    @property
    def passed(self) -> bool:
        return self.cases_run > 0 and self.cases_matched == self.cases_run

    def as_gate(self) -> GateResult:
        if self.cases_run == 0:
            return GateResult(
                name=GateName.DIFFERENTIAL,
                passed=False,
                detail="no differential cases were generated; the task cannot be scored",
                seconds=self.seconds,
            )
        detail = (
            f"{self.cases_matched}/{self.cases_run} cases matched"
            if self.passed
            else "; ".join(f"{d.channel}: {d.detail}" for d in self.divergences[:3])
        )
        return GateResult(
            name=GateName.DIFFERENTIAL, passed=self.passed, detail=detail, seconds=self.seconds
        )


def generate_cases(
    generator_command: str | None,
    *,
    seed: str,
    count: int,
    workdir: Path,
    package_root: Path | None = None,
    timeout: int = 600,
) -> list[DifferentialCase]:
    """Build the hidden input set for this round.

    Procedural generation from the round seed is what makes special-casing
    unprofitable: the concrete inputs did not exist when the artifact was frozen.
    A package with no generator falls back to its sealed corpus, which the caller
    supplies directly.

    The generator command is interpreted relative to ``package_root``, not to the
    scratch directory, so a package can refer to its own tooling by a stable path
    regardless of where the validator happens to be working.
    """
    if not generator_command:
        return []

    out_dir = workdir / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)
    command = f"{generator_command} --seed {seed} --count {count} --out {out_dir}"
    proc = subprocess.run(  # noqa: S603 - validator-owned generator from the task package
        ["/bin/sh", "-c", command],
        cwd=package_root or workdir,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise DifferentialError(f"input generator failed: {proc.stderr.strip()[-400:]}")

    cases: list[DifferentialCase] = []
    for manifest in sorted(out_dir.glob("*.json")):
        data = json.loads(manifest.read_text())
        cases.append(
            DifferentialCase(
                case_id=data.get("case_id", manifest.stem),
                argv=tuple(data.get("argv", ())),
                stdin=bytes.fromhex(data["stdin_hex"]) if data.get("stdin_hex") else b"",
                files={k: bytes.fromhex(v) for k, v in (data.get("files_hex") or {}).items()},
            )
        )
    return cases


def load_sealed_cases(revealed: bytes) -> list[DifferentialCase]:
    """Parse the differential corpus after the timelock opens.

    The bytes come from ``bittensor.timelock.decrypt``; before the reveal round
    nobody — including the validator that sealed them — can read this.
    """
    payload = json.loads(revealed.decode())
    return [
        DifferentialCase(
            case_id=c["case_id"],
            argv=tuple(c.get("argv", ())),
            stdin=bytes.fromhex(c["stdin_hex"]) if c.get("stdin_hex") else b"",
            files={k: bytes.fromhex(v) for k, v in (c.get("files_hex") or {}).items()},
        )
        for c in payload["cases"]
    ]


@dataclass(slots=True)
class DifferentialRunner:
    """Executes both sides against identical hidden inputs and compares."""

    run_command: str
    timeout_seconds: int = 120
    #: Stop after this many divergences; one is already a zero for the task, and
    #: the rest only cost machine time.
    max_reported_divergences: int = 10

    def run(
        self,
        baseline: Workspace,
        candidate: Workspace,
        task: Task,
        cases: list[DifferentialCase],
    ) -> DifferentialReport:
        started = time.monotonic()
        comparator = comparator_for(task.equivalence)

        matched = 0
        divergences: list[Divergence] = []

        for case in cases:
            base_obs = self._observe(baseline, case, task)
            cand_obs = self._observe(candidate, case, task)
            result: ComparisonResult = comparator.compare(base_obs, cand_obs, task.equivalence)
            if result.equivalent:
                matched += 1
            else:
                for d in result.divergences:
                    divergences.append(Divergence(d.channel, f"case {case.case_id}: {d.detail}"))
                if len(divergences) >= self.max_reported_divergences:
                    break

        return DifferentialReport(
            cases_run=len(cases),
            cases_matched=matched,
            divergences=tuple(divergences),
            seconds=time.monotonic() - started,
        )

    def _observe(self, workspace: Workspace, case: DifferentialCase, task: Task) -> Observation:
        """Run one side and collect every declared observable."""
        scratch = workspace.root / ".cf-diff"
        if scratch.exists():
            import shutil as _shutil

            _shutil.rmtree(scratch)
        scratch.mkdir(parents=True)

        for name, content in (case.files or {}).items():
            target = scratch / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        command = self.run_command
        if case.argv:
            command = f"{command} {' '.join(case.argv)}"

        try:
            proc = subprocess.run(  # noqa: S603
                ["/bin/sh", "-c", command],
                cwd=workspace.root,
                input=case.stdin,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired:
            # A timeout is a behaviour difference, not an infrastructure problem:
            # a candidate that hangs where the baseline returned has changed the
            # program's observable behaviour.
            stdout, stderr, code = b"", b"<timeout>", 124

        written: dict[str, bytes] = {}
        if "written_files" in task.equivalence.side_effects:
            for p in sorted(scratch.rglob("*")):
                if p.is_file():
                    written[str(p.relative_to(scratch))] = p.read_bytes()

        state: dict[str, object] = {}
        state_file = scratch / "cf_state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
            except json.JSONDecodeError:
                state = {"_parse_error": True}

        return Observation(
            stdout=stdout, stderr=stderr, exit_code=code, written_files=written, state=state
        )
