"""``cf-miner`` — submit an artifact and inspect what is on chain.

    cf-miner check   --image ghcr.io/you/my-optimizer
    cf-miner submit  --netuid 1 --image ghcr.io/you/my-optimizer \
                     --wallet.name miner --wallet.hotkey default
    cf-miner status  --netuid 1 --wallet.name miner --wallet.hotkey default

Submitting is a one-line operation with a one-line rule behind it: what goes on
chain is a **digest**, never a tag. A tag can be repointed after a round begins,
and the whole competition rests on the artifact being frozen before the tasks
are known.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.spec import SPEC

app = typer.Typer(add_completion=False, help="CompilerForge miner tooling")
console = Console()


@app.command()
def check(
    image: str = typer.Option(..., help="Container repository, without a tag"),
    container_cli: str = typer.Option("docker", help="Container CLI"),
) -> None:
    """Resolve an image to its digest and check it against the artifact rules."""
    import json
    import subprocess

    problems: list[str] = []

    proc = subprocess.run(  # noqa: S603
        [container_cli, "image", "inspect", image, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        console.print(f"[red]Cannot inspect {image}.[/red] Build it first.")
        raise typer.Exit(1)

    info = json.loads(proc.stdout)
    size = int(info.get("Size", 0))
    cap = SPEC.budget.artifact_max_uncompressed_bytes
    if size > cap:
        problems.append(
            f"image is {size / 1024**3:.2f} GiB, the cap is {cap / 1024**3:.0f} GiB"
        )

    repo_digests = info.get("RepoDigests") or []
    digest = repo_digests[0].split("@", 1)[1] if repo_digests else None
    if digest is None:
        problems.append("image has not been pushed, so it has no registry digest yet")

    config = info.get("Config") or {}
    if not (config.get("Entrypoint") or config.get("Cmd")):
        problems.append("image declares no entrypoint")
    if config.get("User", "") in ("", "0", "root", "0:0"):
        problems.append(
            "image runs as root; the sandbox forces a non-root user, so build for one"
        )

    table = Table(title=f"Artifact check · {image}", show_header=False)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("size", f"{size / 1024**3:.2f} GiB (cap {cap / 1024**3:.0f} GiB)")
    table.add_row("digest", digest or "[yellow]not pushed[/yellow]")
    table.add_row("entrypoint", str(config.get("Entrypoint") or config.get("Cmd")))
    table.add_row("user", config.get("User") or "[red]root[/red]")
    console.print(table)

    if problems:
        console.print(
            Panel("\n".join(f"· {p}" for p in problems), title="Problems", border_style="red")
        )
        raise typer.Exit(1)

    console.print(
        Panel(
            "Ready to submit. Remember that the run is network-isolated: your agent "
            "gets the repository, the task contract and — if the task allows it — a "
            "loopback inference proxy. Nothing else.",
            border_style="green",
        )
    )


@app.command()
def submit(
    netuid: int = typer.Option(..., help="Subnet netuid"),
    image: str = typer.Option(..., help="Container repository, without a tag"),
    digest: str = typer.Option(None, help="sha256 digest; resolved from the registry if omitted"),
    version: str = typer.Option("0.1.0", help="Informational agent version"),
    cells: str = typer.Option("generalist", help="Comma-separated cells to enter"),
    wallet_name: str = typer.Option("default", "--wallet.name"),
    wallet_hotkey: str = typer.Option("default", "--wallet.hotkey"),
    network: str = typer.Option("finney", "--subtensor.network"),
    container_cli: str = typer.Option("docker", help="Container CLI"),
) -> None:
    """Commit an artifact digest on chain."""
    import bittensor as bt

    from compilerforge.base.miner import ArtifactResolutionError, BaseMinerNeuron
    from compilerforge.chain.access import ChainAccess, ChainError

    wallet = bt.Wallet(name=wallet_name, hotkey=wallet_hotkey)
    chain = ChainAccess(netuid=netuid, network=network)

    try:
        snapshot = chain.metagraph()
    except ChainError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    hotkey = wallet.hotkey.ss58_address
    if not snapshot.is_registered(hotkey):
        console.print(
            f"[red]{hotkey} is not registered on netuid {netuid}.[/red]\n"
            f"  btcli subnet register --netuid {netuid} "
            f"--wallet.name {wallet_name} --wallet.hotkey {wallet_hotkey}"
        )
        raise typer.Exit(1)

    if digest is None:
        try:
            digest = BaseMinerNeuron.resolve_digest(None, image, container_cli)  # type: ignore[arg-type]
        except ArtifactResolutionError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    commitment = ArtifactCommitment(
        image=image,
        digest=digest,
        agent_version=version,
        cells=tuple(c.strip() for c in cells.split(",") if c.strip()),
    )

    console.print(f"Committing [bold]{commitment.pull_reference()}[/bold]")
    try:
        chain.set_commitment(wallet, commitment.encode())
    except ChainError as exc:
        console.print(f"[red]Commitment failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print("[green]Committed.[/green]")
    console.print(
        "Your artifact competes from the next round whose task-selecting block "
        "postdates this commitment."
    )


@app.command()
def status(
    netuid: int = typer.Option(..., help="Subnet netuid"),
    wallet_name: str = typer.Option("default", "--wallet.name"),
    wallet_hotkey: str = typer.Option("default", "--wallet.hotkey"),
    network: str = typer.Option("finney", "--subtensor.network"),
) -> None:
    """Show this hotkey's on-chain commitment and standing."""
    import bittensor as bt

    from compilerforge.chain.access import ChainAccess, ChainError

    wallet = bt.Wallet(name=wallet_name, hotkey=wallet_hotkey)
    chain = ChainAccess(netuid=netuid, network=network)

    try:
        snapshot = chain.metagraph()
    except ChainError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    hotkey = wallet.hotkey.ss58_address
    uid = snapshot.uid_of(hotkey)
    if uid is None:
        console.print(f"[red]Not registered on netuid {netuid}.[/red]")
        raise typer.Exit(1)

    neuron = next(n for n in snapshot.neurons if n.uid == uid)

    table = Table(title=f"uid {uid} · netuid {netuid}", show_header=False)
    table.add_column("", style="bold")
    table.add_column("")
    table.add_row("hotkey", hotkey)
    table.add_row("stake", f"{neuron.stake:.4f}")
    table.add_row("incentive", f"{neuron.incentive:.6f}")
    table.add_row("emission", f"{neuron.emission:.6f}")

    raw = snapshot.commitments.get(hotkey)
    if not raw:
        table.add_row("artifact", "[yellow]nothing committed[/yellow]")
    else:
        try:
            commitment = ArtifactCommitment.decode(raw)
        except Exception as exc:  # noqa: BLE001
            table.add_row("artifact", f"[red]unparseable: {exc}[/red]")
        else:
            table.add_row("artifact", commitment.pull_reference())
            table.add_row("version", commitment.agent_version)
            table.add_row("cells", ", ".join(commitment.cells))
            table.add_row("committed", commitment.committed_at.isoformat())
            if commitment.iface != SPEC.interface_version:
                table.add_row(
                    "interface",
                    f"[red]{commitment.iface}[/red] — this network runs "
                    f"{SPEC.interface_version}; your artifact is being ignored",
                )

    console.print(table)


if __name__ == "__main__":
    app()
