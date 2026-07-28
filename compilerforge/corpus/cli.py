"""``cf-corpus`` — build, validate and audit the task corpus.

    cf-corpus validate ./corpus
    cf-corpus measure-reference ./corpus/string-split
    cf-corpus manifest ./corpus --snapshot cf-corpus-2026.08
    cf-corpus derive-round --block-hash 0x... --corpus-snapshot ... --spec-digest ...
    cf-corpus seal ./corpus/string-split --hours 36

A task package is only usable once its baseline reproduces deterministically and
its reference optimization has been measured — the reference speedup is what the
capture metric normalises against, and a package without one cannot be scored.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from compilerforge.corpus.package import Corpus, LoadedPackage, inventory_hash
from compilerforge.evaluation.build import Builder, Workspace, apply_patch, toolchain_digest
from compilerforge.evaluation.measurement import MeasurementError, TierARunner
from compilerforge.evaluation.selection import RoundSeed, derive_round
from compilerforge.spec import SPEC

app = typer.Typer(add_completion=False, help="CompilerForge corpus tooling")
console = Console()


@app.command()
def validate(
    corpus_dir: Path = typer.Argument(..., help="Directory of task packages"),
    strict: bool = typer.Option(True, help="Fail on any package problem"),
) -> None:
    """Check every package for the things that void a task at round time."""
    corpus = Corpus.load(corpus_dir, "validate")
    if not corpus.packages:
        console.print(f"[red]No task packages under {corpus_dir}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"{len(corpus.packages)} packages")
    table.add_column("Package")
    table.add_column("Family")
    table.add_column("Hidden", justify="center")
    table.add_column("Profiles", justify="right")
    table.add_column("S_ref", justify="right")
    table.add_column("Problems", overflow="fold")

    failures = 0
    for loaded in sorted(corpus.packages.values(), key=lambda p: p.package.package_id):
        problems = _package_problems(loaded)
        if problems:
            failures += 1
        measured = [
            loaded.reference_speedup(w) for w in loaded.package.workload_profiles
        ]
        known = [s for s in measured if s is not None]
        if not known:
            s_ref_cell = "[red]unmeasured[/red]"
        elif len(set(known)) == 1:
            s_ref_cell = f"{known[0]:.3f}x"
        else:
            s_ref_cell = f"{min(known):.3f}–{max(known):.3f}x"

        table.add_row(
            loaded.package.package_id,
            str(loaded.package.family),
            "yes" if loaded.package.hidden_family else "",
            f"{len(loaded.package.workload_profiles)}",
            s_ref_cell,
            "[red]" + "; ".join(problems) + "[/red]" if problems else "[green]ok[/green]",
        )

    console.print(table)
    console.print(
        f"public: {len(corpus.public())}  ·  hidden: {len(corpus.hidden())}  "
        f"·  manifest {corpus.manifest_hash()[:26]}"
    )

    if not corpus.hidden():
        console.print(
            Panel(
                "This corpus has no hidden family. A round requires at least one "
                "held-out package, so the validator will refuse to derive tasks.",
                border_style="red",
            )
        )
        failures += 1

    if failures and strict:
        raise typer.Exit(1)


def _package_problems(loaded: LoadedPackage) -> list[str]:
    problems: list[str] = []
    pkg = loaded.package

    if not loaded.repo_dir.exists():
        problems.append("no repo/ directory")
    if not loaded.reference_patch.exists():
        problems.append(f"missing {pkg.reference.patch_path}")

    # Every profile a round can draw must have its own measured reference, since
    # capture is normalised per profile. A package-level value is only a fallback.
    for profile in pkg.workload_profiles:
        s_ref = loaded.reference_speedup(profile)
        if s_ref is None:
            problems.append(
                f"profile {profile.name!r} has no measured reference "
                "(run measure-reference)"
            )
        elif s_ref < SPEC.capture.min_reference_speedup:
            problems.append(
                f"profile {profile.name!r} reference {s_ref:.4f} is below the "
                f"{SPEC.capture.min_reference_speedup} minimum; capture cannot normalise"
            )

    from compilerforge.protocol.task import EquivalenceDiscipline

    if pkg.equivalence_discipline == EquivalenceDiscipline.FLOAT_TOLERANCE and not (
        pkg.float_tolerance_ulp or pkg.relative_error_budget
    ):
        problems.append("float discipline with no declared tolerance budget")

    if not pkg.workload_profiles:
        problems.append("no workload profiles")
    elif not any(p.published for p in pkg.workload_profiles):
        problems.append("no published workload profile")

    return problems


@app.command("measure-reference")
def measure_reference(
    package_dir: Path = typer.Argument(..., help="Task package directory"),
    workdir: Path = typer.Option(Path("/tmp/cf-corpus"), help="Scratch directory"),
    write: bool = typer.Option(True, help="Write the results into package.yaml"),
    only_profile: str = typer.Option(None, "--profile", help="Measure only this profile"),
) -> None:
    """Measure the reference optimization on every workload profile.

    Each result defines the 1.0 point on the capture scale for that profile: an
    artifact scoring 1.0 matched the expert patch there. Measured once, at corpus
    build time, and never recomputed during a round.

    Per profile, not per package: the same patch is routinely worth several times
    more on one workload shape than on another, and a single package-wide number
    would over-reward artifacts on the easy profiles and under-reward them on the
    hard ones.
    """
    loaded = LoadedPackage.load(package_dir)
    tier_a = TierARunner()
    if not tier_a.available():
        console.print("[red]valgrind is required to measure a reference.[/red]")
        raise typer.Exit(1)

    profiles = list(loaded.package.workload_profiles)
    if only_profile:
        profiles = [w for w in profiles if w.name == only_profile]
        if not profiles:
            console.print(f"[red]No profile named {only_profile!r}.[/red]")
            raise typer.Exit(1)

    builder = Builder()
    digest = toolchain_digest()
    workdir.mkdir(parents=True, exist_ok=True)

    # The build is identical across profiles — only the benchmark arguments
    # differ — so build each side once and measure many times.
    probe = loaded.build_task(
        task_id="sha256:" + "0" * 64,
        seed="0x0",
        toolchain_digest=digest,
        profile=profiles[0],
    )

    with console.status("Building baseline..."):
        base_ws = Workspace.create(loaded.repo_dir, workdir / "baseline")
        if not builder.build(base_ws, probe, opt_level="-O2").ok:
            console.print("[red]Baseline does not build.[/red]")
            raise typer.Exit(1)

    with console.status("Applying and building the reference patch..."):
        ref_ws = Workspace.create(loaded.repo_dir, workdir / "reference")
        gate = apply_patch(ref_ws, loaded.reference_patch.read_text())
        if not gate.passed:
            console.print(f"[red]Reference patch does not apply: {gate.detail}[/red]")
            raise typer.Exit(1)
        if not builder.build(ref_ws, probe, opt_level="-O2").ok:
            console.print("[red]Reference patch does not build.[/red]")
            raise typer.Exit(1)

    table = Table(title=f"Reference · {loaded.package.package_id}")
    table.add_column("Profile")
    table.add_column("Baseline Ir", justify="right")
    table.add_column("Reference Ir", justify="right")
    table.add_column("S_ref", justify="right")

    measured: dict[str, float] = {}
    too_small: list[str] = []

    for workload in profiles:
        task = loaded.build_task(
            task_id="sha256:" + "0" * 64,
            seed="0x0",
            toolchain_digest=digest,
            profile=workload,
        )
        try:
            with console.status(f"Measuring {workload.name}..."):
                base = tier_a.measure(task.benchmark.command, cwd=base_ws.root)
                ref = tier_a.measure(task.benchmark.command, cwd=ref_ws.root)
                result = tier_a.compare(base, ref)
        except MeasurementError as exc:
            console.print(f"[red]{workload.name}: {exc}[/red]")
            raise typer.Exit(1) from exc

        speedup = result.deterministic_speedup
        measured[workload.name] = speedup
        if speedup < SPEC.capture.min_reference_speedup:
            too_small.append(workload.name)

        table.add_row(
            workload.name,
            f"{result.instructions_baseline:,}",
            f"{result.instructions_candidate:,}",
            f"[red]{speedup:.4f}x[/red]"
            if speedup < SPEC.capture.min_reference_speedup
            else f"{speedup:.6f}x",
        )

    console.print(table)

    if measured:
        spread = max(measured.values()) / min(measured.values())
        if spread > 2.0:
            console.print(
                Panel(
                    f"The reference is worth {spread:.1f}x more on the easiest profile "
                    "than on the hardest. That is exactly why capture normalises per "
                    "profile — but check that every profile measures something you "
                    "meant to measure.",
                    border_style="yellow",
                )
            )

    if too_small:
        console.print(
            Panel(
                f"Profiles {', '.join(too_small)} have a reference below the "
                f"{SPEC.capture.min_reference_speedup} minimum. A reference that is not "
                "itself an improvement cannot normalise anything; either drop those "
                "profiles or drop the package.",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    if write:
        _write_profile_references(package_dir / "package.yaml", measured)
        console.print(f"[green]Wrote {len(measured)} profile reference(s).[/green]")

    base_ws.cleanup()
    ref_ws.cleanup()


def _write_profile_references(manifest: Path, measured: dict[str, float]) -> None:
    """Set s_ref_deterministic on each named profile, preserving comments.

    Edits the YAML as text rather than round-tripping it through a parser: these
    manifests carry the reasoning behind every field, and a dump-and-reload would
    silently delete all of it.
    """
    import re as _re

    lines = manifest.read_text().splitlines()
    out: list[str] = []
    current: str | None = None
    indent = "    "

    def flush(pending: str | None) -> None:
        if pending is None or pending not in measured:
            return
        # Insert before any trailing blank lines, so the value lands inside the
        # profile block it belongs to rather than after a visual separator.
        trailing: list[str] = []
        while out and not out[-1].strip():
            trailing.append(out.pop())
        out.append(f"{indent}s_ref_deterministic: {measured[pending]:.6f}")
        out.extend(reversed(trailing))

    for line in lines:
        name_match = _re.match(r"^(\s*)-\s+name:\s*(\S+)", line)
        if name_match:
            flush(current)
            indent = name_match.group(1) + "  "
            current = name_match.group(2).strip("\"'")
            out.append(line)
            continue

        if current is not None:
            if line.strip() and not line.startswith(indent):
                # A dedent or a new top-level key closes the profile block.
                flush(current)
                current = None
            elif _re.match(rf"^{_re.escape(indent)}s_ref_deterministic:", line):
                # Superseded by the flush above; drop the stale value.
                continue

        out.append(line)

    flush(current)
    manifest.write_text("\n".join(out) + "\n")


@app.command()
def manifest(
    corpus_dir: Path = typer.Argument(..., help="Directory of task packages"),
    snapshot: str = typer.Option(..., help="Snapshot identifier to freeze under"),
    output: Path | None = typer.Option(None, help="Write the manifest here"),
) -> None:
    """Publish the corpus manifest.

    Hidden packages appear as a count only. A miner learns how many held-out
    families a round will draw from, and nothing about which.
    """
    corpus = Corpus.load(corpus_dir, snapshot)
    payload = corpus.manifest()
    text = json.dumps(payload, indent=2, sort_keys=True)

    if output:
        output.write_text(text)
        console.print(f"wrote {output}")
    else:
        console.print_json(text)

    console.print(f"\nmanifest hash: [bold]{corpus.manifest_hash()}[/bold]")


@app.command("derive-round")
def derive_round_command(
    corpus_dir: Path = typer.Argument(..., help="Directory of task packages"),
    block_hash: str = typer.Option(..., help="Block hash that selected the round"),
    block_number: int = typer.Option(0, help="Block number, for the manifest"),
    corpus_snapshot: str = typer.Option(..., help="Corpus snapshot identifier"),
    public_tasks: int = typer.Option(25),
    hidden_tasks: int = typer.Option(3),
) -> None:
    """Re-derive a round's task set from public data.

    This is the check a third party runs against a published audit bundle: the
    same block hash, corpus snapshot and consensus digest must produce a
    byte-identical task set. If it does not, something in the round was not what
    it claimed to be.
    """
    corpus = Corpus.load(corpus_dir, corpus_snapshot)
    seed = RoundSeed(
        block_number=block_number,
        block_hash=block_hash,
        corpus_snapshot=corpus_snapshot,
        spec_digest=SPEC.digest(),
    )
    plan = derive_round(
        seed=seed,
        corpus=corpus,
        toolchain_digest=toolchain_digest(),
        public_task_count=public_tasks,
        hidden_task_count=hidden_tasks,
    )

    table = Table(title=f"Round from block {block_number}")
    table.add_column("Package")
    table.add_column("Profile")
    table.add_column("Hidden", justify="center")
    table.add_column("Seed")
    for selected in plan.tasks:
        table.add_row(
            selected.package_id,
            selected.profile.name,
            "yes" if selected.hidden else "",
            selected.task.seed[:18] + "...",
        )
    console.print(table)
    console.print(f"\nmanifest hash: [bold]{plan.manifest_hash()}[/bold]")


@app.command()
def seal(
    package_dir: Path = typer.Argument(..., help="Task package directory"),
    hours: float = typer.Option(36.0, help="Hours until the material becomes readable"),
    count: int = typer.Option(500, help="Differential cases to generate"),
    seed: str | None = typer.Option(
        None,
        help="Generator seed. Omit for a fresh random secret. A fixed, public seed "
        "lets anyone reproduce these cases from the shipped generator, which "
        "defeats the seal — only pass one if it is secret and high-entropy.",
    ),
) -> None:
    """Seal a package's hidden differential corpus with a timelock.

    Before the reveal round nobody can decrypt this — including whoever sealed it.
    That turns "the validator did not peek at the hidden tests" from a statement
    of trust into something a third party can check against a public beacon.

    The seal is only as secret as its seed. The generator ships in the public
    tree, so a fixed, guessable seed lets a miner regenerate these exact cases
    without ever decrypting anything — the timelock then protects an envelope
    whose contents are already known. The default is therefore a fresh random
    secret, and a seal is single-use: rotate it on every corpus refresh.
    """
    import secrets

    from compilerforge.chain.sealed import seal_differential_cases
    from compilerforge.evaluation.differential import generate_cases

    loaded = LoadedPackage.load(package_dir)
    generator = loaded.package.input_generator
    if not generator:
        console.print("[red]This package declares no input generator.[/red]")
        raise typer.Exit(1)

    if seed is None:
        seed = "0x" + secrets.token_hex(32)
        console.print("using a fresh random seal seed")
    else:
        console.print(
            "[yellow]note:[/yellow] a fixed seed only stays hidden if it is secret and "
            "high-entropy — a public or guessable seed lets a miner regenerate these "
            "cases from the shipped generator without waiting for the reveal"
        )

    cases = generate_cases(
        generator,
        seed=seed,
        count=count,
        workdir=package_dir / ".seal-work",
        package_root=package_dir,
    )
    envelope = seal_differential_cases(
        cases, label=loaded.package.package_id, hours=hours
    )
    target = package_dir / "inputs" / "hidden.sealed"
    target.parent.mkdir(parents=True, exist_ok=True)
    envelope.write(target)

    console.print(f"sealed {len(cases)} cases to {target}")
    console.print(f"reveal round: [bold]{envelope.reveal_round}[/bold]")
    console.print(
        "Publish this file now. Anyone can archive it; nobody can open it until the "
        "beacon reaches that round."
    )


@app.command("inventory-hash")
def inventory_hash_command(
    repo_dir: Path = typer.Argument(..., help="Repository directory"),
    globs: str = typer.Option("tests/**/*", help="Comma-separated test globs"),
) -> None:
    """Compute the test inventory hash for a package manifest."""
    patterns = tuple(g.strip() for g in globs.split(",") if g.strip())
    console.print(inventory_hash(repo_dir, patterns))


if __name__ == "__main__":
    app()
