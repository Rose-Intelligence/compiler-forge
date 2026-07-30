"""Verify one patch off the validator host, inside a container.

Scoring a patch means applying, building and executing arbitrary native code the
miner wrote. That must not run on the host that holds the wallet and the revealed
hidden inputs. This module serialises one ``(patch, task)`` pair, runs the
identical evaluation inside a hardened, network-none container with no wallet
mounted, and reads back the score artifact — the same containment the agent gets
during production, extended to the phase that was left on the host.

The in-container entrypoint (:func:`evaluate_from_spec`) and the serialiser
(:func:`write_spec`) live together so the wire format has a single owner. The
validator-owned tooling is rebuilt inside the container: it is not miner data, so
reconstructing it there produces a byte-identical artifact.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from compilerforge.evaluation.differential import DifferentialCase
from compilerforge.evaluation.pipeline import (
    CandidatePatch,
    EvaluationContext,
    Evaluator,
)
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.protocol.task import Task


def _case_to_dict(case: DifferentialCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "argv": list(case.argv),
        "stdin": base64.b64encode(case.stdin).decode(),
        "files": (
            {k: base64.b64encode(v).decode() for k, v in case.files.items()}
            if case.files is not None
            else None
        ),
    }


def _case_from_dict(d: dict[str, Any]) -> DifferentialCase:
    return DifferentialCase(
        case_id=d["case_id"],
        argv=tuple(d.get("argv", [])),
        stdin=base64.b64decode(d.get("stdin", "")),
        files=(
            {k: base64.b64decode(v) for k, v in d["files"].items()}
            if d.get("files") is not None
            else None
        ),
    )


def write_spec(
    spec_dir: Path,
    *,
    candidate: CandidatePatch,
    task: Task,
    package_dir: Path,
    profile_name: str,
    hidden: bool,
    cases: list[DifferentialCase],
    corpus_snapshot: str,
    verifier_hotkey: str,
    fuzz_seconds: int,
) -> None:
    """Write everything the in-container entrypoint needs to reproduce one pair.

    The task package itself is not written here — it is mounted read-only inside
    the container at ``package_dir`` — only the pair-specific inputs are.
    """
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.json").write_text(
        json.dumps(
            {
                "candidate": {
                    "artifact_digest": candidate.artifact_digest,
                    "patch": candidate.patch,
                    "patch_digest": candidate.patch_digest,
                    "producer_uid": candidate.producer_uid,
                },
                "task": task.model_dump(mode="json"),
                "package_dir": str(package_dir),
                "profile_name": profile_name,
                "hidden": hidden,
                "cases": [_case_to_dict(c) for c in cases],
                "corpus_snapshot": corpus_snapshot,
                "verifier_hotkey": verifier_hotkey,
                "fuzz_seconds": fuzz_seconds,
            }
        )
    )


def evaluate_from_spec(spec_dir: Path, workdir: Path) -> ScoreArtifact:
    """Reconstruct one pair from its spec and score it. The container entrypoint.

    Rebuilds the validator-owned tooling locally — it is not miner data — and runs
    the same ``Evaluator.evaluate`` the host would, so the artifact is identical to
    an in-process run.
    """
    from compilerforge.corpus.package import LoadedPackage
    from compilerforge.evaluation.baseline import BaselineBuilder
    from compilerforge.evaluation.build import Builder
    from compilerforge.evaluation.measurement import TierARunner
    from compilerforge.evaluation.selection import SelectedTask

    spec = json.loads((spec_dir / "spec.json").read_text())

    task = Task.model_validate(spec["task"])
    package = LoadedPackage.load(Path(spec["package_dir"]))
    profile = next(
        p for p in package.package.workload_profiles if p.name == spec["profile_name"]
    )
    selected = SelectedTask(
        task=task, package=package, profile=profile, hidden=spec["hidden"]
    )
    cases = [_case_from_dict(c) for c in spec["cases"]]

    builder = Builder()
    tier_a = TierARunner()
    ctx = EvaluationContext(
        workdir=workdir / "eval",
        builder=builder,
        tier_a=tier_a,
        tier_b=None,
        baselines=BaselineBuilder(
            builder=builder, tier_a=tier_a, cache_dir=workdir / "baselines"
        ),
        corpus_snapshot=spec["corpus_snapshot"],
        verifier_hotkey=spec["verifier_hotkey"],
        tier_b_available=False,
        fuzz_seconds=spec["fuzz_seconds"],
    )

    c = spec["candidate"]
    candidate = CandidatePatch(
        artifact_digest=c["artifact_digest"],
        patch=c["patch"],
        patch_digest=c["patch_digest"],
        producer_uid=c.get("producer_uid"),
    )
    return Evaluator(ctx=ctx).evaluate(candidate, selected, cases=cases)


class SandboxVerifyError(RuntimeError):
    """The sandboxed verification could not produce a score artifact."""


def run_in_sandbox(
    candidate: CandidatePatch,
    selected: Any,
    cases: list[DifferentialCase],
    ctx: EvaluationContext,
    *,
    image: str,
    container_cli: str = "docker",
    timeout_s: int = 1800,
) -> ScoreArtifact:
    """Score one pair inside a hardened container, off the host.

    The container has the task package mounted read-only, no network, no wallet,
    all capabilities dropped and a non-root user — so building and running the
    miner's patch cannot touch the validator's key or reach out. It returns the
    same artifact an in-process run would (proven by the serialisation parity).
    """
    import shutil
    import subprocess
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="cf-verify-"))
    try:
        spec_dir = work / "spec"
        out_dir = work / "out"
        out_dir.mkdir(parents=True)
        # /output is the only writable bind mount and the container runs non-root,
        # so it must be group/other-writable for the artifact to land.
        out_dir.chmod(0o777)

        write_spec(
            spec_dir,
            candidate=candidate,
            task=selected.task,
            package_dir=Path("/work/pkg"),  # the container-side mount point
            profile_name=selected.profile.name,
            hidden=selected.hidden,
            cases=cases,
            corpus_snapshot=ctx.corpus_snapshot,
            verifier_hotkey=ctx.verifier_hotkey,
            fuzz_seconds=ctx.fuzz_seconds,
        )

        pkg_root = Path(selected.package.root).resolve()
        args = [
            container_cli, "run", "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            # valgrind needs syscalls the default seccomp profile blocks; offset by
            # no-caps / non-root / no-network.
            "--security-opt=seccomp=unconfined",
            "--read-only",
            "--user=65534:65534",
            "--pids-limit=512",
            "--memory=4g", "--memory-swap=4g",
            "--tmpfs", "/tmp:rw,exec,nosuid,size=1g",
            "--tmpfs", "/work/scratch:rw,exec,nosuid,size=2g",
            "--mount", f"type=bind,src={pkg_root},dst=/work/pkg,readonly",
            "--mount", f"type=bind,src={spec_dir.resolve()},dst=/work/spec,readonly",
            # /work/out is writable (the default for a bind mount): the artifact lands here.
            "--mount", f"type=bind,src={out_dir.resolve()},dst=/work/out",
            image,
            "python3", "-m", "compilerforge.sandbox.verify",
            "/work/spec", "/work/scratch", "/work/out/artifact.json",
        ]
        try:
            proc = subprocess.run(  # noqa: S603
                args, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxVerifyError(
                f"sandboxed verification exceeded {timeout_s}s"
            ) from exc

        artifact_file = out_dir / "artifact.json"
        if not artifact_file.exists():
            raise SandboxVerifyError(
                f"sandboxed verification produced no artifact (exit {proc.returncode}): "
                f"{proc.stderr[-2000:]}"
            )
        return ScoreArtifact.model_validate_json(artifact_file.read_text())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    """Container entrypoint: ``python -m compilerforge.sandbox.verify SPEC WORK OUT``."""
    import sys

    spec_dir, workdir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    artifact = evaluate_from_spec(spec_dir, workdir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(artifact.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
