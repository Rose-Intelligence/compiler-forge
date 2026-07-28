"""``cf-eval`` — the miner's local development loop.

    cf-eval preflight                       what this machine can and cannot do
    cf-eval agent  ./corpus/zlib-inflate --entrypoint "python3 agent.py"
    cf-eval patch  ./corpus/zlib-inflate --patch my.diff
    cf-eval gates                           the gate sequence, in order
    cf-eval spec                            the active consensus constants

Everything here runs the same code a validator runs. If a patch passes locally and
fails on chain, look first at the two differences the evaluator documents: hidden
inputs and sandbox isolation.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from compilerforge.corpus.package import LoadedPackage
from compilerforge.evaluation.build import toolchain_digest
from compilerforge.evaluation.measurement import HostCalibration, TierARunner
from compilerforge.sandbox.isolation import detect_runtime
from compilerforge.sdk.evaluator import LocalEvaluator, LocalResult
from compilerforge.spec import SPEC
from compilerforge.utils.misc import human_duration

app = typer.Typer(add_completion=False, help="CompilerForge local evaluator")
console = Console()

DEFAULT_WORKDIR = Path("~/.compilerforge/sdk").expanduser()


@app.command()
def preflight() -> None:
    """Report what this machine can measure, and what it cannot."""
    table = Table(title="Local evaluator readiness", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    tier_a = TierARunner()
    table.add_row(
        "Deterministic tier",
        "[green]ready[/green]" if tier_a.available() else "[red]missing[/red]",
        "valgrind found"
        if tier_a.available()
        else "install valgrind — this is the measurement that decides scores",
    )

    problems = HostCalibration.probe().problems()
    table.add_row(
        "Wall-clock tier",
        "[green]calibrated[/green]" if not problems else "[yellow]uncalibrated[/yellow]",
        "; ".join(problems)
        if problems
        else "governor pinned, turbo off, cores isolated",
    )

    runtime = detect_runtime()
    table.add_row(
        "Container runtime",
        "[green]hardened[/green]" if runtime.is_hardened else "[yellow]shared kernel[/yellow]",
        runtime.value,
    )

    table.add_row("Toolchain digest", "", toolchain_digest())
    table.add_row("Consensus digest", "", SPEC.digest())
    table.add_row("Interface version", "", SPEC.interface_version)

    console.print(table)

    if problems:
        console.print(
            Panel(
                "Wall-clock numbers from an uncalibrated host are not comparable to a "
                "validator's. They are still useful as a smoke test — a patch that is "
                "dramatically slower here will be slower there too — but do not tune "
                "against them. Tune against the deterministic tier.",
                title="On uncalibrated wall-clock",
                border_style="yellow",
            )
        )


@app.command()
def agent(
    package: Path = typer.Argument(..., help="Task package directory"),
    entrypoint: str = typer.Option(..., help="Command that runs your agent"),
    seed: str = typer.Option("0x1234", help="Task seed; try several"),
    profile: str | None = typer.Option(None, help="Workload profile name"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, help="Scratch directory"),
    tier_b: bool = typer.Option(False, help="Also measure wall-clock"),
    fuzz_seconds: int = typer.Option(60, help="Fuzzing budget per candidate"),
    json_output: bool = typer.Option(False, "--json", help="Emit the score artifact"),
) -> None:
    """Run your agent against a task, then evaluate whatever it produced."""
    loaded = LoadedPackage.load(package)
    evaluator = LocalEvaluator(
        workdir=workdir, tier_b_enabled=tier_b, fuzz_seconds=fuzz_seconds
    )
    selected = evaluator.build_task(loaded, seed=seed, profile_name=profile)

    console.print(f"[bold]{loaded.package.package_id}[/bold] · profile {selected.profile.name}")
    console.print(f"objective {selected.task.benchmark.objective} · seed {seed}\n")

    with console.status("Running agent..."):
        result = evaluator.evaluate_agent(shlex.split(entrypoint), selected)

    if result.run is not None:
        problems = evaluator.interface_problems(result.run, selected)
        if problems:
            console.print(
                Panel(
                    "\n".join(f"· {p}" for p in problems),
                    title="Interface violations — each of these is a zero",
                    border_style="red",
                )
            )
        _print_run(result)

    _print_result(result, json_output=json_output)
    raise typer.Exit(0 if result.ok or result.run and not result.run.produced_patch else 1)


@app.command()
def patch(
    package: Path = typer.Argument(..., help="Task package directory"),
    patch_file: Path = typer.Option(..., "--patch", help="Unified diff to evaluate"),
    seed: str = typer.Option("0x1234", help="Task seed"),
    profile: str | None = typer.Option(None, help="Workload profile name"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, help="Scratch directory"),
    tier_b: bool = typer.Option(False, help="Also measure wall-clock"),
    json_output: bool = typer.Option(False, "--json", help="Emit the score artifact"),
) -> None:
    """Evaluate an existing patch without running an agent."""
    loaded = LoadedPackage.load(package)
    evaluator = LocalEvaluator(workdir=workdir, tier_b_enabled=tier_b)
    selected = evaluator.build_task(loaded, seed=seed, profile_name=profile)

    with console.status("Running the gate sequence..."):
        result = evaluator.evaluate_patch(patch_file.read_text(), selected)

    _print_result(result, json_output=json_output)
    raise typer.Exit(0 if result.ok else 1)


@app.command()
def onboard(
    project: Path = typer.Argument(..., help="Directory holding your source tree"),
    out: Path = typer.Option(..., "--out", help="Where to write the task package"),
    package_id: str = typer.Option("my-project", help="Identifier for this package"),
    family: str = typer.Option("cli_utilities", help="Workload family"),
    bench_args: str = typer.Option("", help="Arguments passed to the benchmark"),
    force: bool = typer.Option(False, help="Overwrite an existing package"),
) -> None:
    """Turn a source tree into a task package this pipeline can measure.

    Detects the build system, reads the test and benchmark targets out of the
    build definition, works out which directories a candidate may rewrite, and
    decides whether the benchmark can be measured between markers or has to be
    measured whole. Whatever it could not determine is reported rather than
    guessed.
    """
    import shutil

    from compilerforge.corpus.scaffold import ScaffoldError, detect, write_package

    if out.exists():
        if not force:
            console.print(f"[red]{out} already exists. Pass --force to overwrite.[/red]")
            raise typer.Exit(2)
        shutil.rmtree(out)

    try:
        out.mkdir(parents=True)
        shutil.copytree(project, out / "repo")
        detected = detect(out / "repo")
    except ScaffoldError as exc:
        shutil.rmtree(out, ignore_errors=True)
        console.print(f"[red]Cannot onboard this project:[/red] {exc}")
        raise typer.Exit(1) from exc

    argv = bench_args.split() if bench_args else []

    table = Table(title="Detected")
    table.add_column("Field")
    table.add_column("Value", overflow="fold")
    for key, value in detected.to_dict().items():
        if key in ("warnings", "missing", "ready"):
            continue
        table.add_row(key, str(value))
    console.print(table)

    if detected.missing:
        console.print("[red]Not enough information to build a package:[/red]")
        for item in detected.missing:
            console.print(f"  • {item}")
        shutil.rmtree(out, ignore_errors=True)
        raise typer.Exit(1)

    write_package(
        detected, out, package_id=package_id, family=family, benchmark_args=argv,
        differential_argument_sets=[argv] if argv else None,
    )

    for warning in detected.warnings:
        console.print(f"[yellow]note:[/yellow] {warning}")

    console.print(
        f"\n[green]Package written to {out}[/green]\n"
        f"Verify a patch against it with:\n"
        f"  cf-eval verify {out} --patch your.diff"
    )


@app.command()
def verify(
    package: Path = typer.Argument(..., help="Task package directory"),
    patch_file: Path = typer.Option(..., "--patch", help="Unified diff to verify"),
    seed: str = typer.Option("0x1234", help="Task seed"),
    profile: str | None = typer.Option(None, help="Workload profile name"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, help="Scratch directory"),
    tier_b: bool = typer.Option(False, help="Also measure wall-clock"),
    json_output: bool = typer.Option(False, "--json", help="Emit the artifact"),
) -> None:
    """Check a patch is correct and measure it, without scoring it.

    Runs the same gates and the same measurement a validator runs, but computes
    no capture — so the package does not need an expert reference patch. This is
    the mode for someone optimizing their own code, who wants to know whether the
    change is safe and how much it saved, not what it would be paid.
    """
    loaded = LoadedPackage.load(package)
    evaluator = LocalEvaluator(workdir=workdir, tier_b_enabled=tier_b)
    selected = evaluator.build_task(loaded, seed=seed, profile_name=profile)

    with console.status("Running the gate sequence..."):
        result = evaluator.verify_patch(patch_file.read_text(), selected)

    _print_result(result, json_output=json_output)
    if not json_output:
        console.print(
            "[dim]Verification only: no capture and no score were computed, so "
            "this result is not a consensus measurement.[/dim]"
        )
    raise typer.Exit(0 if result.score is not None and result.score.all_gates_passed() else 1)


@app.command()
def gates() -> None:
    """Print the gate sequence a candidate must survive, in order."""
    table = Table(title="Evaluation gates")
    table.add_column("#", justify="right")
    table.add_column("Gate")
    table.add_column("What it checks", overflow="fold")

    rows = [
        (
            "1",
            "baseline_stable",
            "The unmodified repository measures identically across repeats. If not, "
            "the task is void for everyone — not a zero for you.",
        ),
        (
            "2",
            "patch_hygiene",
            "Patch size and scope. Editing the build definition or a test file "
            "fails here.",
        ),
        ("3", "patch_applies", "The diff applies cleanly to the pinned revision."),
        (
            "4",
            "build",
            "The project builds with the pinned toolchain and no forbidden flags.",
        ),
        (
            "5",
            "test_inventory",
            "Every test file is still present and unmodified, verified by hash.",
        ),
        ("6", "api_abi", "Public headers and ABI conform to the task contract."),
        ("7", "public_tests", "The project's own test suite still passes."),
        (
            "8",
            "differential",
            "Baseline and candidate agree on hidden inputs under the declared "
            "equivalence discipline.",
        ),
        ("9", "asan", "No memory error under AddressSanitizer."),
        ("10", "ubsan", "No undefined behaviour under UndefinedBehaviorSanitizer."),
        ("11", "fuzz", "No crash under coverage-guided fuzzing."),
        (
            "12",
            "second_opt_level",
            "Behaviour is identical when rebuilt at a different optimization level.",
        ),
    ]
    for row in rows:
        table.add_row(*row)

    console.print(table)
    console.print(
        Panel(
            "Every gate is hard. Failing one produces zero for the task, not a "
            "reduced score. Only after all of them does measurement begin.\n\n"
            "Returning no patch at all is [bold]not[/bold] a failure: an honest empty "
            "result scores above a rejected one, and it still earns from the floor pool.",
            border_style="blue",
        )
    )


@app.command()
def spec() -> None:
    """Print the active consensus constants."""
    console.print(f"[bold]digest[/bold] {SPEC.digest()}")
    console.print(f"[bold]version[/bold] {SPEC.spec_version}  ·  interface {SPEC.interface_version}")
    console.print(f"[bold]hardware class[/bold] {SPEC.hardware_class}\n")

    weights = Table(title="Score components")
    weights.add_column("Component")
    weights.add_column("Weight", justify="right")
    c = SPEC.components
    for name, value in (
        ("deterministic capture", c.deterministic_capture),
        ("peak memory", c.peak_memory),
        ("tail latency", c.tail_latency),
        ("hidden generalisation", c.hidden_generalisation),
        ("compile time", c.compile_time),
        ("cross-validator agreement", c.cross_validator_agreement),
    ):
        weights.add_row(name, f"{value:.0%}")
    console.print(weights)

    limits = Table(title="Budgets and limits")
    limits.add_column("Limit")
    limits.add_column("Value", justify="right")
    b = SPEC.budget
    limits.add_row("artifact size", f"{b.artifact_max_uncompressed_bytes / 1024**3:.0f} GiB")
    limits.add_row("wall-clock per task", human_duration(b.default_wall_seconds))
    limits.add_row("model tokens per task", f"{b.default_model_token_budget:,}")
    limits.add_row("changed files per patch", str(b.max_patch_changed_files))
    limits.add_row("added lines per patch", str(b.max_patch_added_lines))
    limits.add_row("capture cap", f"{SPEC.capture.c_max:.1f}x the reference")
    console.print(limits)


def _print_run(result: LocalResult) -> None:
    run = result.run
    if run is None:
        return
    table = Table(title="Agent run", show_header=False)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("exit code", str(run.exit_code))
    table.add_row("wall time", human_duration(run.wall_seconds))
    table.add_row("patch", "produced" if run.produced_patch else "none (honest empty result)")
    if run.report is not None:
        table.add_row("candidates tried", str(run.report.candidate_count))
        table.add_row("tokens used", f"{run.report.budget_used.model_tokens:,}")
        claimed = run.report.self_measurement.local_speedup_estimate
        if claimed is not None:
            table.add_row("self-estimate", f"{claimed:.4f}x (never scored)")
    console.print(table)


def _print_result(result: LocalResult, *, json_output: bool) -> None:
    if json_output:
        # --json is a machine-readable contract: emit a JSON object for every
        # outcome, never a human panel. An honest empty result (the agent found
        # nothing safe to change) and a voided task are legitimate outcomes, not
        # the absence of one.
        if result.score is not None:
            console.print_json(result.score.model_dump_json())
        elif result.voided_reason:
            console.print_json(json.dumps({"voided": True, "void_reason": result.voided_reason}))
        else:
            console.print_json(
                json.dumps({"honest_null": True, "empty": True, "gates": [], "tier_a": None, "voided": False})
            )
        return

    if result.voided_reason:
        console.print(
            Panel(
                result.voided_reason,
                title="Task voided — this is a task problem, not your patch",
                border_style="yellow",
            )
        )
        return

    if result.score is None:
        console.print("[yellow]No patch to evaluate.[/yellow]")
        return

    table = Table(title="Gate sequence")
    table.add_column("Gate")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for gate in result.score.gates:
        table.add_row(
            str(gate.name),
            "[green]pass[/green]" if gate.passed else "[red]FAIL[/red]",
            gate.detail[:120],
        )
    console.print(table)

    if result.score.tier_a is not None:
        a = result.score.tier_a
        console.print(
            f"\n[bold]deterministic[/bold] {a.instructions_baseline:,} -> "
            f"{a.instructions_candidate:,} instructions "
            f"({a.deterministic_speedup:.4f}x, lower bound {a.speedup_lcb:.4f}x)"
        )
    if result.score.tier_b is not None:
        b = result.score.tier_b
        console.print(
            f"[bold]wall-clock[/bold] {b.median_speedup:.4f}x median, "
            f"lower bound {b.speedup_lcb_95:.4f}x, "
            f"sign agreement {'yes' if b.sign_agreement else 'NO'}"
        )
    if result.score.reference is not None:
        r = result.score.reference
        console.print(
            f"[bold]capture[/bold] {r.capture:.3f} of the reference {r.s_ref:.4f}x "
            f"({r.capture:.0%} of what the expert patch achieved)"
        )

    style = "green" if result.ok else "red"
    console.print(
        Panel(result.summary(), border_style=style, subtitle=human_duration(result.seconds))
    )


@app.command("write-task")
def write_task(
    package: Path = typer.Argument(..., help="Task package directory"),
    seed: str = typer.Option("0x1234", help="Task seed"),
    profile: str | None = typer.Option(None, help="Workload profile name"),
    output: Path = typer.Option(Path("task.json"), help="Where to write it"),
) -> None:
    """Write a task.json your agent can be run against by hand."""
    loaded = LoadedPackage.load(package)
    evaluator = LocalEvaluator(workdir=DEFAULT_WORKDIR)
    selected = evaluator.build_task(loaded, seed=seed, profile_name=profile)
    output.write_text(selected.task.to_json())
    console.print(f"wrote {output}")
    console.print(json.dumps(json.loads(selected.task.to_json()), indent=2)[:800])


if __name__ == "__main__":
    app()
