"""Stage 2 — patch application, build and interface conformance.

Cheap gates run first. Most candidates die here, which is what makes the whole
evaluation affordable: a validator that fuzzes before checking
whether the patch applies is buying nothing.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.corpus.package import inventory_hash
from compilerforge.protocol.score import GateName, GateResult
from compilerforge.protocol.task import Task
from compilerforge.spec import SPEC


class BuildError(RuntimeError):
    pass


@dataclass(slots=True)
class Workspace:
    """A throwaway copy of the repository that a patch can be applied to.

    Baseline builds are immutable per (revision, toolchain_digest) and should be
    built once, ever. Candidate builds are not, so they get a fresh
    tree every time; sharing one would let a leftover object file from a previous
    candidate change a later measurement.
    """

    root: Path
    source: Path

    @classmethod
    def create(cls, source: Path, root: Path) -> Workspace:
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(source, root, symlinks=True)
        return cls(root=root, source=source)

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class PatchStats:
    changed_files: tuple[str, ...]
    added_lines: int
    removed_lines: int

    @property
    def touched_count(self) -> int:
        return len(self.changed_files)


_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def parse_patch(patch: str) -> PatchStats:
    files = tuple(sorted(set(_DIFF_FILE_RE.findall(patch))))
    added = sum(
        1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    return PatchStats(changed_files=files, added_lines=added, removed_lines=removed)


def check_patch_hygiene(patch: str, task: Task) -> GateResult:
    """Patch size and scope limits (maintainability).

    Expressed as an eligibility rule rather than a subjective quality score,
    because a subjective positive component is an attack surface.
    """
    started = time.monotonic()
    stats = parse_patch(patch)
    problems: list[str] = []

    if stats.touched_count > SPEC.budget.max_patch_changed_files:
        problems.append(
            f"touches {stats.touched_count} files, cap is {SPEC.budget.max_patch_changed_files}"
        )
    if stats.added_lines > SPEC.budget.max_patch_added_lines:
        problems.append(
            f"adds {stats.added_lines} lines, cap is {SPEC.budget.max_patch_added_lines}"
        )

    # A patch that edits the build system can change the flags it is measured
    # under. That is not an optimization; it is measurement manipulation.
    build_files = {"CMakeLists.txt", "Makefile", "meson.build", "configure.ac", "setup.py"}
    for f in stats.changed_files:
        name = Path(f).name
        if name in build_files:
            problems.append(f"modifies the build definition {f}")
        if "/tests/" in f or f.startswith("tests/") or name.startswith("test_"):
            problems.append(f"modifies validator-owned test file {f}")

    return GateResult(
        name=GateName.PATCH_HYGIENE,
        passed=not problems,
        detail=(
            "; ".join(problems)
            if problems
            else f"{stats.touched_count} files, "
            f"+{stats.added_lines}/-{stats.removed_lines}"
        ),
        seconds=time.monotonic() - started,
    )


def apply_patch(workspace: Workspace, patch: str) -> GateResult:
    """Apply the unified diff against the pinned revision."""
    started = time.monotonic()
    if not patch.strip():
        return GateResult(
            name=GateName.PATCH_APPLIES,
            passed=False,
            detail="empty patch",
            seconds=time.monotonic() - started,
        )

    patch_file = workspace.root.parent / "candidate.patch"
    patch_file.write_text(patch)

    # --check first so a partially applied patch never leaves a half-patched tree.
    for args in (
        ["git", "apply", "--check", "--unsafe-paths", str(patch_file)],
        ["git", "apply", "--unsafe-paths", str(patch_file)],
    ):
        proc = subprocess.run(  # noqa: S603
            args, cwd=workspace.root, capture_output=True, text=True, check=False
        )
        if proc.returncode != 0:
            # Fall back to `patch`, which tolerates some offsets git rejects.
            fallback = subprocess.run(  # noqa: S603
                ["patch", "-p1", "--batch", "--forward", "-i", str(patch_file)],
                cwd=workspace.root,
                capture_output=True,
                text=True,
                check=False,
            )
            if fallback.returncode != 0:
                return GateResult(
                    name=GateName.PATCH_APPLIES,
                    passed=False,
                    detail=(proc.stderr or fallback.stderr).strip()[:400],
                    seconds=time.monotonic() - started,
                )
            break

    return GateResult(
        name=GateName.PATCH_APPLIES,
        passed=True,
        detail="applied cleanly",
        seconds=time.monotonic() - started,
    )


@dataclass(frozen=True, slots=True)
class BuildResult:
    ok: bool
    seconds: float
    log_tail: str
    #: Feeds the 5% compile-time component: a patch that trades
    #: developer time for runtime is not free.
    compile_seconds: float = 0.0


@dataclass(slots=True)
class Builder:
    """Runs the pinned toolchain. The task contract owns the flags, not the patch."""

    timeout_seconds: int = 3600
    extra_env: dict[str, str] = field(default_factory=dict)

    def build(
        self,
        workspace: Workspace,
        task: Task,
        *,
        opt_level: str | None = None,
        sanitizers: tuple[str, ...] = (),
        extra_cflags: str = "",
    ) -> BuildResult:
        flags = [extra_cflags] if extra_cflags else []
        if opt_level:
            flags.append(opt_level)
        for san in sanitizers:
            flags.append(f"-fsanitize={san}")
        if sanitizers:
            # Without these a sanitizer report is a line number short of useful.
            flags += ["-fno-omit-frame-pointer", "-g"]

        env = {
            **os.environ,
            **self.extra_env,
            "CFLAGS": " ".join(flags),
            "CXXFLAGS": " ".join(flags),
            "CF_TOOLCHAIN_DIGEST": task.build.toolchain_digest,
        }
        if sanitizers:
            env["LDFLAGS"] = " ".join(f"-fsanitize={s}" for s in sanitizers)

        started = time.monotonic()
        proc = subprocess.run(  # noqa: S603 - the command comes from the task package
            ["/bin/sh", "-c", task.build.command],
            cwd=workspace.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        elapsed = time.monotonic() - started
        log = (proc.stdout + proc.stderr)[-4000:]

        if proc.returncode == 0 and task.build.forbidden_flags:
            offending = [f for f in task.build.forbidden_flags if f in log]
            if offending:
                return BuildResult(
                    ok=False,
                    seconds=elapsed,
                    log_tail=f"forbidden flags observed in build: {', '.join(offending)}",
                    compile_seconds=elapsed,
                )

        return BuildResult(
            ok=proc.returncode == 0, seconds=elapsed, log_tail=log, compile_seconds=elapsed
        )


def check_test_inventory(workspace: Workspace, task: Task, globs: tuple[str, ...]) -> GateResult:
    """Required tests are still present and enabled.

    Verified against a hash of the inventory, not by counting files, so deleting a
    test and adding an empty one with the same name does not pass.
    """
    started = time.monotonic()
    actual = inventory_hash(workspace.root, globs)
    passed = actual == task.tests.inventory_hash
    return GateResult(
        name=GateName.TEST_INVENTORY,
        passed=passed,
        detail="unchanged" if passed else f"expected {task.tests.inventory_hash}, got {actual}",
        seconds=time.monotonic() - started,
    )


def run_public_tests(workspace: Workspace, task: Task, *, timeout: int = 1800) -> GateResult:
    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", task.tests.public_command],
        cwd=workspace.root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return GateResult(
        name=GateName.PUBLIC_TESTS,
        passed=proc.returncode == 0,
        detail="passed" if proc.returncode == 0 else (proc.stdout + proc.stderr)[-400:],
        seconds=time.monotonic() - started,
    )


def check_api_abi(
    baseline_workspace: Workspace,
    candidate_workspace: Workspace,
    task: Task,
    *,
    header_globs: tuple[str, ...] = ("include/**/*.h", "include/**/*.hpp", "**/*.h"),
) -> GateResult:
    """Public API and ABI conformance.

    V1 compares the declared public headers byte-for-byte after normalising
    whitespace and comments. That is deliberately conservative: it rejects some
    harmless header edits, and it never accepts a signature change. A real ABI
    diff (abidiff over the built shared objects) is the Phase 2 upgrade and is
    what the ``abidiff`` branch below uses when the tool is present.
    """
    started = time.monotonic()

    if shutil.which("abidiff"):
        result = _abidiff(baseline_workspace, candidate_workspace)
        if result is not None:
            passed, detail = result
            return GateResult(
                name=GateName.API_ABI, passed=passed, detail=detail, seconds=time.monotonic() - started
            )

    changed: list[str] = []
    for pattern in header_globs:
        for base_header in sorted(baseline_workspace.root.glob(pattern)):
            rel = base_header.relative_to(baseline_workspace.root)
            cand_header = candidate_workspace.root / rel
            if not cand_header.exists():
                changed.append(f"{rel}: removed")
                continue
            if _normalise_source(base_header.read_bytes()) != _normalise_source(
                cand_header.read_bytes()
            ):
                changed.append(f"{rel}: modified")

    return GateResult(
        name=GateName.API_ABI,
        passed=not changed,
        detail="; ".join(changed[:5]) if changed else "public headers unchanged",
        seconds=time.monotonic() - started,
    )


def _abidiff(baseline: Workspace, candidate: Workspace) -> tuple[bool, str] | None:
    """Compare built shared objects when libabigail is available."""
    base_libs = sorted(baseline.root.rglob("*.so"))
    if not base_libs:
        return None
    problems: list[str] = []
    for lib in base_libs:
        rel = lib.relative_to(baseline.root)
        other = candidate.root / rel
        if not other.exists():
            problems.append(f"{rel}: missing in candidate")
            continue
        proc = subprocess.run(  # noqa: S603
            ["abidiff", str(lib), str(other)], capture_output=True, text=True, check=False
        )
        # abidiff exit code bit 2 (ABI change) is the one that matters.
        if proc.returncode & 0b100:
            problems.append(f"{rel}: {proc.stdout.strip()[:200]}")
    return (not problems, "; ".join(problems[:5]) if problems else "ABI unchanged (abidiff)")


_COMMENT_RE = re.compile(rb"//[^\n]*|/\*.*?\*/", re.DOTALL)
_WS_RE = re.compile(rb"\s+")


def _normalise_source(data: bytes) -> bytes:
    return _WS_RE.sub(b" ", _COMMENT_RE.sub(b" ", data)).strip()


def toolchain_digest() -> str:
    """Identify the pinned toolchain.

    Part of the comparability tuple. Two validators on different Clang builds are
    not measuring the same program, and must not compare scores.
    """
    parts: list[str] = []
    for tool in ("clang", "clang++", "ld", "cmake"):
        path = shutil.which(tool)
        if not path:
            parts.append(f"{tool}=absent")
            continue
        proc = subprocess.run(  # noqa: S603
            [path, "--version"], capture_output=True, text=True, check=False
        )
        parts.append(f"{tool}={proc.stdout.strip().splitlines()[0] if proc.stdout else 'unknown'}")
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()
