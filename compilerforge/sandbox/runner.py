"""Executing a miner artifact.

One command, one input file, two output files. The narrow interface is the point:
it keeps the sandbox surface small and it keeps the harness comparison fair.

The artifact never receives wallet material, a Docker socket, hidden task data or
a route to anywhere except a metered loopback proxy. That is enforced here, not
documented and hoped for.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.protocol.report import AgentReport
from compilerforge.protocol.task import Task
from compilerforge.sandbox.isolation import (
    IsolationError,
    IsolationProfile,
    NetworkMode,
    Phase,
    default_profile,
)
from compilerforge.spec import SPEC
from compilerforge.utils.logging import logger


class ArtifactError(RuntimeError):
    """An interface violation. A zero, not a negotiation."""


@dataclass(slots=True)
class ArtifactRun:
    """The result of executing one artifact against one task."""

    exit_code: int
    patch: str
    report: AgentReport | None
    wall_seconds: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False
    tokens_used: int = 0
    #: Digest of the patch bytes. This is what gets published so every validator
    #: verifies the same patch.
    patch_digest: str = ""
    #: Why report.json could not be read, when it could not. Carried so the
    #: interface check can tell a miner what was wrong rather than only that
    #: something was.
    report_error: str | None = None

    @property
    def produced_patch(self) -> bool:
        return bool(self.patch.strip())


@dataclass(slots=True)
class ArtifactRunner:
    """Runs digest-pinned OCI images under a fixed isolation profile."""

    workdir: Path
    container_cli: str = "docker"
    #: Loopback address of the validator-owned inference proxy, when configured.
    proxy_url: str | None = None
    #: Development-only: permit a shared-kernel runtime for authoritative phases.
    allow_unhardened: bool = False
    _pulled: set[str] = field(default_factory=set)

    async def pull(self, commitment: ArtifactCommitment) -> None:
        """Pull by digest, never by tag.

        Also enforces the artifact size cap: a large image is the natural hiding
        place for an embedded answer table or an undeclared model.
        """
        ref = commitment.pull_reference()
        if ref in self._pulled:
            return

        code, out = await _run(self.container_cli, "pull", ref)
        if code != 0:
            raise ArtifactError(f"cannot pull {ref}: {out.strip()[:400]}")

        size = await self._image_size(ref)
        cap = SPEC.budget.artifact_max_uncompressed_bytes
        if size > cap:
            raise ArtifactError(
                f"artifact is {size / 1024**3:.2f} GiB, cap is {cap / 1024**3:.0f} GiB"
            )
        self._pulled.add(ref)

    async def _image_size(self, ref: str) -> int:
        code, out = await _run(self.container_cli, "image", "inspect", ref, "--format", "{{.Size}}")
        if code != 0:
            raise ArtifactError(f"cannot inspect {ref}")
        try:
            return int(out.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ArtifactError(f"unparseable image size for {ref}: {out!r}") from exc

    async def run(
        self,
        commitment: ArtifactCommitment,
        task: Task,
        repo_src: Path,
        *,
        profile: IsolationProfile | None = None,
    ) -> ArtifactRun:
        """Execute one artifact against one task instance.

        The repository is mounted read-only and the artifact gets exactly one
        writable directory. Anything it wants to keep, it writes there.
        """
        run_root = self.workdir / f"run-{task.task_id.removeprefix('sha256:')[:16]}-{int(time.time())}"
        repo_dir = run_root / "repo"
        task_dir = run_root / "task"
        out_dir = run_root / "output"
        for d in (task_dir, out_dir):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo_src, repo_dir, dirs_exist_ok=True)

        (task_dir / "task.json").write_text(task.to_json())

        network = NetworkMode.PROXY if task.network == "proxy" else NetworkMode.NONE
        prof = profile or default_profile(
            phase=Phase.OPTIMIZE,
            resources=task.resources,
            wall_seconds=task.benchmark.max_wall_seconds,
            network=network,
        )
        prof.assert_safe(allow_unhardened=self.allow_unhardened)

        mounts = {
            str(repo_dir): "/workspace/repo",
            str(task_dir): "/task",
            str(out_dir): "/output",
        }
        prof.assert_mounts_safe(mounts)

        # /output is the artifact's only writable mount, but it is created by this
        # process and the artifact runs as an unprivileged non-root user. Without
        # this the agent cannot write its own report, and the interface check reads
        # a validator-side permission error as a miner-side violation — a zero for
        # work that was actually done.
        _grant_output_access(out_dir, prof.run_as_user)

        args = [self.container_cli, "run", "--rm"]
        args += prof.container_args()
        args += [f"--volume={repo_dir}:/workspace/repo:ro"]
        args += [f"--volume={task_dir}:/task:ro"]
        args += [f"--volume={out_dir}:/output:rw"]
        # Writable scratch that vanishes with the container, so the read-only
        # rootfs does not force the agent to be creative about where it works.
        #
        # `exec` is required, not an oversight: the rootfs is read-only, so this is
        # the only place an agent can put a binary, and an optimization agent that
        # cannot run what it just compiled cannot reproduce a baseline at all. The
        # isolation that matters here is the runtime boundary and `nosuid`, not
        # noexec on a tmpfs the agent already fully controls.
        args += ["--tmpfs=/tmp:rw,exec,nosuid,size=2g"]
        args += ["--env", f"CF_SEED={task.seed}"]
        args += ["--env", f"CF_TASK_ID={task.task_id}"]
        args += ["--env", f"CF_INTERFACE_VERSION={task.interface_version}"]
        args += ["--env", f"CF_TOKEN_BUDGET={task.resources.model_token_budget}"]
        if network is NetworkMode.PROXY:
            if not self.proxy_url:
                raise IsolationError("task requests proxy network but no proxy_url is configured")
            args += ["--env", f"CF_INFERENCE_URL={self.proxy_url}"]
        args += [commitment.pull_reference()]

        started = time.monotonic()
        code, combined, timed_out = await _run_with_timeout(args, timeout=prof.wall_seconds)
        elapsed = time.monotonic() - started

        patch_path = out_dir / "patch.diff"
        report_path = out_dir / "report.json"
        patch = patch_path.read_text(errors="replace") if patch_path.exists() else ""

        report: AgentReport | None = None
        report_error: str | None = None
        if report_path.exists():
            try:
                report = AgentReport.model_validate_json(report_path.read_text())
            except Exception as exc:  # noqa: BLE001
                # A malformed report is an interface violation, and the gate layer
                # scores it as such — but the parse error is the only thing that
                # tells a miner *why*, so it is carried on the run rather than
                # discarded here.
                report_error = f"{type(exc).__name__}: {exc}"[:300]
                logger.info("artifact wrote an unparseable report.json: %s", report_error)
        else:
            report_error = "no /output/report.json was written"

        return ArtifactRun(
            exit_code=code,
            patch=patch,
            report=report,
            wall_seconds=elapsed,
            stdout_tail=combined[-4000:],
            timed_out=timed_out,
            tokens_used=report.budget_used.model_tokens if report else 0,
            patch_digest="sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
            report_error=report_error,
        )

    def verify_interface(self, run: ArtifactRun, task: Task) -> None:
        """An interface violation is a zero, not a negotiation."""
        if run.timed_out:
            raise ArtifactError(f"artifact exceeded its {task.benchmark.max_wall_seconds}s budget")
        if run.report is None:
            raise ArtifactError(
                f"missing or malformed /output/report.json ({run.report_error})"
                if run.report_error
                else "missing or malformed /output/report.json"
            )
        if run.report.interface_version != task.interface_version:
            raise ArtifactError(
                f"report declares interface {run.report.interface_version}, "
                f"task requires {task.interface_version}"
            )
        if run.report.task_id != task.task_id:
            raise ArtifactError("report task_id does not match the task it was given")
        budget = task.resources.model_token_budget
        if run.report.budget_used.model_tokens > budget:
            raise ArtifactError(
                f"self-reported token use {run.report.budget_used.model_tokens} exceeds budget {budget}"
            )


def _grant_output_access(out_dir: Path, run_as_user: str) -> None:
    """Make the output mount writable by the unprivileged user the artifact runs as.

    Prefers chown, which keeps the directory private to that user. A validator
    running without the privilege to chown falls back to a permissive mode: the
    directory holds only this artifact's own output, and the alternative is
    failing every run.
    """
    uid_s, _, gid_s = run_as_user.partition(":")
    try:
        os.chown(out_dir, int(uid_s), int(gid_s or uid_s))
    except (PermissionError, ValueError, OSError):
        out_dir.chmod(0o777)


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def _run_with_timeout(args: list[str], *, timeout: int) -> tuple[int, str, bool]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace"), False
    except TimeoutError:
        # Reap the whole process group, not just the entrypoint.
        proc.kill()
        try:
            await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            pass
        return 124, "", True


def local_run(
    entrypoint: list[str],
    task: Task,
    repo_src: Path,
    workdir: Path,
) -> ArtifactRun:
    """Run an agent as a local subprocess, for the miner SDK only.

    This deliberately does *not* provide the isolation guarantees of ``run``. It
    exists so a miner can iterate on a laptop against the same file contract the
    validator uses. A validator must never call it.
    """
    run_root = workdir
    repo_dir = run_root / "repo"
    task_dir = run_root / "task"
    out_dir = run_root / "output"
    for d in (task_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    shutil.copytree(repo_src, repo_dir)
    (task_dir / "task.json").write_text(task.to_json())

    env = {
        "CF_SEED": task.seed,
        "CF_TASK_ID": task.task_id,
        "CF_INTERFACE_VERSION": task.interface_version,
        "CF_TOKEN_BUDGET": str(task.resources.model_token_budget),
        "CF_LOCAL_REPO": str(repo_dir),
        "CF_LOCAL_TASK": str(task_dir),
        "CF_LOCAL_OUTPUT": str(out_dir),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(run_root),
    }

    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - miner-side tooling, not the validator path
        entrypoint,
        cwd=run_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=task.benchmark.max_wall_seconds,
        check=False,
    )
    elapsed = time.monotonic() - started

    patch_path = out_dir / "patch.diff"
    report_path = out_dir / "report.json"
    patch = patch_path.read_text(errors="replace") if patch_path.exists() else ""
    report = None
    report_error: str | None = None
    if report_path.exists():
        try:
            report = AgentReport.model_validate(json.loads(report_path.read_text()))
        except Exception as exc:  # noqa: BLE001
            report_error = f"{type(exc).__name__}: {exc}"[:300]
    else:
        report_error = "no /output/report.json was written"

    return ArtifactRun(
        exit_code=proc.returncode,
        patch=patch,
        report=report,
        wall_seconds=elapsed,
        stdout_tail=proc.stdout[-4000:],
        stderr_tail=proc.stderr[-4000:],
        patch_digest="sha256:" + hashlib.sha256(patch.encode()).hexdigest(),
        report_error=report_error,
    )
