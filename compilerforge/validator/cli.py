"""``cf-validator`` — check readiness before a hotkey is at stake.

    cf-validator preflight --netuid 1
    cf-validator preflight --netuid 1 --subtensor.network test --no-chain
    cf-validator hyperparameters --netuid 1

The validator neuron already refuses to start without Valgrind or a hardened
container runtime. That is the right behaviour, but it is a poor way to *find
out*: the operator learns one problem per restart, after the wallet is loaded
and a chain connection is open.

This reports everything at once, before any of that, and separates the two
questions an operator actually has:

  Can this host produce comparable scores?   — toolchain, runtime, corpus
  Is the subnet configured to accept them?   — tempo, commit-reveal, uid cap

A failure to *check* is reported as a failure, never as a pass. "We could not
read the chain" and "the chain is fine" must never look the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from compilerforge.corpus.package import Corpus
from compilerforge.evaluation.build import toolchain_digest
from compilerforge.evaluation.measurement import HostCalibration, TierARunner
from compilerforge.sandbox.isolation import detect_runtime
from compilerforge.spec import SPEC

app = typer.Typer(add_completion=False, help="CompilerForge validator tooling")
console = Console()


class Status(StrEnum):
    OK = "ok"
    #: Works, but this validator contributes less than it could.
    DEGRADED = "degraded"
    #: The validator will refuse to start, or will produce incomparable scores.
    BLOCKING = "blocking"

    @property
    def marker(self) -> str:
        return {
            Status.OK: "[green]ok[/green]",
            Status.DEGRADED: "[yellow]degraded[/yellow]",
            Status.BLOCKING: "[red]BLOCKING[/red]",
        }[self]


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str
    #: What to do about it. Empty when there is nothing to do.
    remedy: str = ""


def check_deterministic_tier() -> Check:
    """The consensus-bearing measurement. Without it there is no valid score."""
    runner = TierARunner()
    if runner.available():
        return Check("Deterministic tier", Status.OK, "valgrind found")
    return Check(
        "Deterministic tier",
        Status.BLOCKING,
        "valgrind is not installed",
        "sudo apt-get install valgrind — this is the measurement that decides "
        "scores, and a validator without it must self-exclude rather than emit "
        "weights nobody can reproduce",
    )


def check_sandbox_runtime(*, allow_unhardened: bool) -> Check:
    runtime = detect_runtime()
    if runtime.is_hardened:
        return Check("Sandbox runtime", Status.OK, runtime.value)
    detail = f"{runtime.value} shares the host kernel"
    if allow_unhardened:
        return Check(
            "Sandbox runtime",
            Status.DEGRADED,
            detail + ", override set",
            "acceptable for local development only — never on a machine holding "
            "a hotkey",
        )
    return Check(
        "Sandbox runtime",
        Status.BLOCKING,
        detail,
        "install gVisor (runsc) or Kata Containers; this host runs anonymous "
        "code alongside a hotkey",
    )


def check_wall_clock_host() -> Check:
    """Optional. A validator without one still carries a full consensus weight."""
    problems = HostCalibration.probe().problems()
    if not problems:
        return Check("Wall-clock tier", Status.OK, "calibrated")
    return Check(
        "Wall-clock tier",
        Status.DEGRADED,
        "; ".join(problems),
        "optional — leave --measurement.tier_b off and this validator still "
        "participates fully through the deterministic tier",
    )


def check_memory_measurement() -> Check:
    """Peak RSS feeds a scored component, so its absence is worth naming."""
    import shutil

    if shutil.which("/usr/bin/time"):
        return Check("Memory measurement", Status.OK, "/usr/bin/time found")
    return Check(
        "Memory measurement",
        Status.DEGRADED,
        "/usr/bin/time is not installed",
        f"sudo apt-get install time — without it the "
        f"{SPEC.components.peak_memory:.0%} memory component is forfeited for "
        "every candidate this validator scores",
    )


def check_corpus(corpus_dir: Path, private_dir: Path | None = None) -> list[Check]:
    """A corpus that cannot supply a round is a blocking problem, not a warning.

    The held-out families live outside the public corpus, so preflight checks the
    same merged view a round runs against: the public dir plus the private dir.
    """
    if not corpus_dir.exists():
        return [
            Check(
                "Corpus",
                Status.BLOCKING,
                f"{corpus_dir} does not exist",
                "clone the corpus, or point --corpus.dir at it",
            )
        ]

    extra = (private_dir,) if private_dir and private_dir.exists() else ()
    try:
        corpus = Corpus.load(corpus_dir, "preflight", extra_roots=extra)
    except Exception as exc:  # noqa: BLE001 - reported, never absorbed
        return [Check("Corpus", Status.BLOCKING, f"could not load: {exc}")]

    checks = [
        Check(
            "Corpus",
            Status.OK if corpus.packages else Status.BLOCKING,
            f"{len(corpus.public())} public, {len(corpus.hidden())} hidden",
            "" if corpus.packages else "no task packages found",
        )
    ]

    if not corpus.hidden():
        checks.append(
            Check(
                "Hidden family",
                Status.BLOCKING,
                "corpus has no held-out package",
                "provision the private corpus and point --corpus.private_dir at it; "
                "a round requires at least one held-out package or task derivation refuses",
            )
        )

    unmeasured = [
        f"{p.package.package_id}:{w.name}"
        for p in corpus.packages.values()
        for w in p.package.workload_profiles
        if p.reference_speedup(w) is None
    ]
    if unmeasured:
        checks.append(
            Check(
                "Reference speedups",
                Status.BLOCKING,
                f"{len(unmeasured)} profile(s) unmeasured: {', '.join(unmeasured[:3])}",
                "run cf-corpus measure-reference — capture has nothing to "
                "normalise against without it, so those tasks void",
            )
        )

    return checks


def check_chain(netuid: int, network: str) -> list[Check]:
    """Whether the subnet is configured to accept this validator's weights."""
    from compilerforge.chain.access import ChainAccess, ChainError
    from compilerforge.chain.hyperparameters import (
        effective_activity_cutoff,
        heartbeat_required,
    )

    chain = ChainAccess(netuid=netuid, network=network)

    try:
        block = chain.current_block()
    except ChainError as exc:
        return [
            Check(
                "Chain connection",
                Status.BLOCKING,
                str(exc),
                "a validator that cannot read the chain produces no weights",
            )
        ]

    checks = [Check("Chain connection", Status.OK, f"{network}, head at block {block:,}")]

    from compilerforge.chain.hyperparameters import check_live

    problems = check_live(chain)
    checks.append(
        Check(
            "Subnet configuration",
            Status.OK if not problems else Status.BLOCKING,
            "matches the plan" if not problems else problems[0],
        )
    )
    for extra in problems[1:]:
        checks.append(Check("", Status.BLOCKING, extra))

    try:
        params = chain.hyperparameters()
        tempo = int(params.get("tempo") or 0)
    except (ChainError, TypeError, ValueError):
        tempo = 0

    if tempo:
        cutoff = effective_activity_cutoff(tempo)
        checks.append(
            Check(
                "Activity cutoff",
                Status.OK,
                f"{cutoff:,} blocks; heartbeat "
                f"{'required' if heartbeat_required(tempo) else 'optional'}",
            )
        )

    return checks


@app.command()
def preflight(
    netuid: int = typer.Option(..., help="Subnet netuid"),
    network: str = typer.Option("finney", "--subtensor.network", help="Network or endpoint"),
    corpus_dir: Path = typer.Option(Path("./corpus"), "--corpus.dir", help="Public task packages"),
    private_dir: Path = typer.Option(
        None, "--corpus.private_dir", help="Held-out task packages, provisioned separately"
    ),
    chain: bool = typer.Option(True, "--chain/--no-chain", help="Also check the subnet"),
    allow_unhardened: bool = typer.Option(
        False, "--sandbox.allow_unhardened_runtime", help="Permit a shared-kernel runtime"
    ),
) -> None:
    """Report everything that would stop this validator, in one pass."""
    checks: list[Check] = [
        check_deterministic_tier(),
        check_sandbox_runtime(allow_unhardened=allow_unhardened),
        check_wall_clock_host(),
        check_memory_measurement(),
        *check_corpus(corpus_dir, private_dir),
    ]
    if chain:
        checks.extend(check_chain(netuid, network))

    table = Table(title=f"Validator preflight · netuid {netuid}")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    for check in checks:
        table.add_row(check.name, check.status.marker, check.detail)

    table.add_row("", "", "")
    table.add_row("Toolchain digest", "", toolchain_digest())
    table.add_row("Consensus digest", "", SPEC.digest())
    table.add_row("Interface", "", SPEC.interface_version)
    console.print(table)

    blocking = [c for c in checks if c.status is Status.BLOCKING]
    degraded = [c for c in checks if c.status is Status.DEGRADED]

    remedies = [c for c in checks if c.remedy]
    if remedies:
        console.print(
            Panel(
                "\n\n".join(f"[bold]{c.name or 'also'}[/bold]\n{c.remedy}" for c in remedies),
                title="What to do",
                border_style="yellow" if not blocking else "red",
            )
        )

    if blocking:
        console.print(
            f"\n[red]{len(blocking)} blocking issue(s).[/red] This validator would "
            "either refuse to start or produce scores nobody else can reproduce."
        )
        raise typer.Exit(1)

    if degraded:
        console.print(
            f"\n[yellow]Ready, with {len(degraded)} degraded check(s).[/yellow] "
            "This validator will participate fully in consensus and contribute "
            "less evidence than a fully equipped one."
        )
        raise typer.Exit(0)

    console.print("\n[green]Ready.[/green]")


@app.command()
def hyperparameters(
    netuid: int = typer.Option(..., help="Subnet netuid"),
    network: str = typer.Option("finney", "--subtensor.network", help="Network or endpoint"),
) -> None:
    """Compare live subnet hyperparameters against the plan."""
    from compilerforge.chain.access import ChainAccess
    from compilerforge.chain.hyperparameters import HYPERPARAMETER_PLAN, check_live

    chain = ChainAccess(netuid=netuid, network=network)

    table = Table(title=f"Hyperparameter plan · netuid {netuid}")
    table.add_column("Parameter", style="bold")
    table.add_column("Chain default")
    table.add_column("CompilerForge")
    table.add_column("Why", overflow="fold")
    for setting in HYPERPARAMETER_PLAN:
        table.add_row(
            setting.name, setting.chain_default, setting.compilerforge, setting.reason
        )
    console.print(table)

    problems = check_live(chain)
    if problems:
        console.print(
            Panel(
                "\n".join(f"· {p}" for p in problems),
                title="Live configuration differs from the plan",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    console.print("\n[green]Live configuration matches the plan.[/green]")


if __name__ == "__main__":
    app()
