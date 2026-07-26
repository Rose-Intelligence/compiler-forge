"""The container path, executed rather than mocked.

Every other test of :mod:`compilerforge.sandbox.runner` substitutes the container
for a stand-in, which is why two defects that made *every* artifact run fail sat
undetected behind a green suite:

* ``/output`` was created by the validator process and mounted read-write, but
  the artifact runs as an unprivileged user and could not write to it;
* the scratch tmpfs was mounted ``noexec``, so an agent could not run the
  benchmark it had just compiled — and with a read-only rootfs there is nowhere
  else to put a binary.

Both surfaced as "no report.json was written", which the interface check scores
as a miner-side violation. A validator-side defect was therefore charged to the
miner as a zero.

The only way to catch that class of bug is to start a real container, so these
tests do. They are ``slow`` and skip when there is no usable runtime.

    pytest -m slow -k sandbox_container
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.protocol.task import ResourceContract
from compilerforge.sandbox.isolation import NetworkMode, Phase, default_profile
from compilerforge.sandbox.runner import ArtifactRunner

pytestmark = pytest.mark.slow

#: Small, always-present in any registry cache a developer already has. The point
#: of these tests is the mount and tmpfs contract, not the agent.
PROBE_IMAGE = "docker.io/library/debian:bookworm-slim"


def _cli() -> str | None:
    return shutil.which("docker") or shutil.which("podman")


def _daemon_reachable(cli: str) -> bool:
    try:
        return subprocess.run(
            [cli, "info"], capture_output=True, timeout=60, check=False
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _image_present(cli: str) -> bool:
    try:
        return subprocess.run(
            [cli, "image", "inspect", PROBE_IMAGE],
            capture_output=True, timeout=60, check=False,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_CLI = _cli()
requires_container = pytest.mark.skipif(
    _CLI is None or not _daemon_reachable(_CLI) or not _image_present(_CLI),
    reason=f"needs a reachable container daemon with {PROBE_IMAGE} available locally",
)


class _LocalImage(ArtifactCommitment):
    """A commitment resolved to a local image, since nothing is pushed in tests."""

    def pull_reference(self) -> str:
        return PROBE_IMAGE


def _commitment() -> _LocalImage:
    return _LocalImage(image="local/probe", digest="sha256:" + "0" * 64)


def _profile(wall_seconds: int = 120):
    # RUNC is deliberate: these assert the *mount contract*, which is identical
    # on every runtime, and requiring gVisor would skip them on most machines.
    # Phase.PREPARE keeps assert_safe from rejecting the unhardened runtime --
    # the hardened-runtime rule itself is covered in test_security.py.
    return default_profile(
        phase=Phase.PREPARE,
        resources=ResourceContract(
            cpu_cores=1, ram_gb=2, disk_gb=8, pids_max=128, model_token_budget=0
        ),
        wall_seconds=wall_seconds,
        network=NetworkMode.NONE,
    )


def _task(tmp_path: Path):
    """A minimal real Task, built the way the evaluator builds one."""
    from compilerforge.corpus.package import LoadedPackage
    from compilerforge.sdk.evaluator import LocalEvaluator

    corpus = Path(__file__).resolve().parent.parent / "corpus" / "string-split"
    loaded = LoadedPackage.load(corpus)
    evaluator = LocalEvaluator(workdir=tmp_path / "eval")
    selected = evaluator.build_task(loaded, seed="0xabcd")
    return selected.task, loaded.repo_dir


# ---------------------------------------------------------------------------
# the output mount
# ---------------------------------------------------------------------------


@requires_container
def test_an_unprivileged_artifact_can_write_its_report(tmp_path):
    """The regression: /output is the artifact's only writable mount.

    The artifact runs as ``run_as_user``, not as the validator, so a directory
    the validator merely created is not one the artifact can write. Before the
    fix this raised PermissionError inside the container and the run was scored
    as a missing report.
    """
    task, repo = _task(tmp_path)
    profile = _profile()
    runner = ArtifactRunner(workdir=tmp_path / "runs")
    runner.workdir.mkdir(parents=True, exist_ok=True)

    # Exactly what the interface requires, written the way an agent writes it.
    report = json.dumps(
        {
            "interface_version": task.interface_version,
            "task_id": task.task_id,
            "agent_version": "probe",
            "objective": task.benchmark.objective,
            "changed_files": [],
            "claimed_strategy": [],
            "candidate_count": 0,
            "selected_candidate": None,
            "rejected_reasons": {},
            "self_measurement": {"local_speedup_estimate": None, "method": "callgrind"},
            "budget_used": {"wall_seconds": 0.0, "model_tokens": 0},
            "notes": "probe",
        }
    )
    assert "'" not in report, "the shell command below single-quotes this payload"

    run = asyncio.run(
        _run_script(
            runner, task, repo, profile,
            ["sh", "-c", f": > /output/patch.diff && printf '%s' '{report}' > /output/report.json"],
        )
    )

    assert run.report_error is None, (
        f"artifact could not write /output: {run.report_error!r}. "
        "The output mount must be writable by the user the artifact runs as."
    )
    assert run.report is not None
    runner.verify_interface(run, task)


@requires_container
def test_the_output_mount_is_owned_by_the_artifact_user(tmp_path):
    """Ownership, asserted directly rather than through a successful write.

    A permissive mode would also let the write succeed, so this pins the
    property the fix is actually meant to establish.
    """
    task, repo = _task(tmp_path)
    profile = _profile()
    runner = ArtifactRunner(workdir=tmp_path / "runs")
    runner.workdir.mkdir(parents=True, exist_ok=True)

    run = asyncio.run(
        _run_script(
            runner, task, repo, profile,
            ["sh", "-c", "test -w /output && echo WRITABLE || echo REFUSED"],
        )
    )
    assert "WRITABLE" in run.stdout_tail, (
        "the artifact user cannot write /output; it saw: " + run.stdout_tail[-300:]
    )


# ---------------------------------------------------------------------------
# the scratch tmpfs
# ---------------------------------------------------------------------------


@requires_container
def test_an_agent_can_execute_what_it_compiled(tmp_path):
    """The rootfs is read-only, so /tmp is the only place a binary can go.

    An optimization agent has to run the benchmark it just built. Mounting the
    one writable path ``noexec`` makes reproducing a baseline impossible, and the
    failure it produces is misleading -- Valgrind reports an ``mmap ... UME``
    error that reads like a runtime incompatibility.

    Docker adds ``noexec`` to ``--tmpfs`` unless ``exec`` is passed explicitly,
    so omitting ``noexec`` is not enough and this must be asserted on the built
    argument list as well as in the container.
    """
    task, repo = _task(tmp_path)
    profile = _profile()
    runner = ArtifactRunner(workdir=tmp_path / "runs")
    runner.workdir.mkdir(parents=True, exist_ok=True)

    run = asyncio.run(
        _run_script(
            runner, task, repo, profile,
            [
                "sh", "-c",
                "printf '#!/bin/sh\\necho RAN\\n' > /tmp/p && chmod +x /tmp/p && /tmp/p",
            ],
        )
    )
    assert "RAN" in run.stdout_tail, (
        "a binary written to the scratch tmpfs could not be executed; "
        "the agent saw: " + run.stdout_tail[-300:]
    )


def test_the_scratch_tmpfs_is_mounted_exec():
    """Cheap guard on the flag itself, so a regression fails the fast suite too."""
    import inspect

    from compilerforge.sandbox import runner as runner_mod

    source = inspect.getsource(runner_mod.ArtifactRunner.run)
    tmpfs = [line for line in source.splitlines() if "--tmpfs=/tmp" in line]
    assert tmpfs, "the scratch tmpfs mount disappeared"
    assert "noexec" not in tmpfs[0], "the agent's only writable path must not be noexec"
    assert "exec" in tmpfs[0], "Docker defaults --tmpfs to noexec unless exec is explicit"
    assert "nosuid" in tmpfs[0], "nosuid is the part of the tmpfs hardening that must stay"


# ---------------------------------------------------------------------------


async def _run_script(runner, task, repo, profile, argv):
    """Run ``argv`` in the probe image through the real ArtifactRunner.

    Overriding the entrypoint is the only deviation from the production path;
    every mount, limit and flag comes from ``ArtifactRunner.run`` unchanged.
    """
    real_exec = asyncio.create_subprocess_exec

    async def patched(*args, **kwargs):
        args = list(args)
        # Insert the entrypoint override just before the image reference, which
        # ArtifactRunner appends last.
        image_at = len(args) - 1
        args[image_at:image_at] = ["--entrypoint", argv[0]]
        args = args + argv[1:]
        return await real_exec(*args, **kwargs)

    asyncio.create_subprocess_exec = patched
    try:
        return await runner.run(_commitment(), task, repo, profile=profile)
    finally:
        asyncio.create_subprocess_exec = real_exec
