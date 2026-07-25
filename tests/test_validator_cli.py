"""Validator preflight.

The point of preflight is to report *everything* wrong at once, before a wallet
is loaded, and to distinguish "this validator cannot produce valid scores" from
"this validator will contribute less evidence than a fully equipped one".

Those two must never collapse into each other: treating a degraded host as
blocking keeps honest validators off the subnet, and treating a blocking host as
degraded puts incomparable weights on chain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from compilerforge.validator.cli import (
    Status,
    check_corpus,
    check_deterministic_tier,
    check_memory_measurement,
    check_sandbox_runtime,
    check_wall_clock_host,
)

CORPUS = Path(__file__).resolve().parent.parent / "corpus"


def test_a_missing_deterministic_tier_blocks(monkeypatch):
    """Without it there is no consensus-bearing measurement, so no valid score."""
    monkeypatch.setattr(
        "compilerforge.evaluation.measurement.TierARunner.available", lambda self: False
    )
    check = check_deterministic_tier()
    assert check.status is Status.BLOCKING
    assert "valgrind" in check.remedy


def test_a_present_deterministic_tier_passes(monkeypatch):
    monkeypatch.setattr(
        "compilerforge.evaluation.measurement.TierARunner.available", lambda self: True
    )
    assert check_deterministic_tier().status is Status.OK


def test_a_shared_kernel_runtime_blocks_by_default(monkeypatch):
    from compilerforge.sandbox.isolation import Runtime

    monkeypatch.setattr("compilerforge.validator.cli.detect_runtime", lambda: Runtime.RUNC)
    check = check_sandbox_runtime(allow_unhardened=False)
    assert check.status is Status.BLOCKING
    assert "gVisor" in check.remedy


def test_the_override_downgrades_it_rather_than_hiding_it(monkeypatch):
    """Development needs an escape hatch; it must still be visible in the report."""
    from compilerforge.sandbox.isolation import Runtime

    monkeypatch.setattr("compilerforge.validator.cli.detect_runtime", lambda: Runtime.RUNC)
    check = check_sandbox_runtime(allow_unhardened=True)
    assert check.status is Status.DEGRADED
    assert "never on a machine holding" in check.remedy


def test_a_hardened_runtime_passes(monkeypatch):
    from compilerforge.sandbox.isolation import Runtime

    monkeypatch.setattr("compilerforge.validator.cli.detect_runtime", lambda: Runtime.RUNSC)
    assert check_sandbox_runtime(allow_unhardened=False).status is Status.OK


def test_an_uncalibrated_wall_clock_host_is_degraded_not_blocking():
    """A validator without one still carries a full consensus weight."""
    assert check_wall_clock_host().status in (Status.OK, Status.DEGRADED)


def test_missing_memory_measurement_names_what_is_forfeited(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    check = check_memory_measurement()
    assert check.status is Status.DEGRADED
    assert "15%" in check.remedy


def test_a_missing_corpus_blocks():
    checks = check_corpus(Path("/definitely/not/here"))
    assert checks[0].status is Status.BLOCKING
    assert "does not exist" in checks[0].detail


def test_the_shipped_corpus_passes_preflight():
    checks = check_corpus(CORPUS)
    blocking = [c for c in checks if c.status is Status.BLOCKING]
    assert not blocking, [f"{c.name}: {c.detail}" for c in blocking]


def test_a_corpus_with_no_hidden_family_blocks(tmp_path):
    """A round requires a held-out package, so derivation would refuse."""
    import shutil

    (tmp_path / "only-public").mkdir()
    shutil.copytree(CORPUS / "string-split", tmp_path / "only-public", dirs_exist_ok=True)

    checks = check_corpus(tmp_path)
    names = {c.name for c in checks if c.status is Status.BLOCKING}
    assert "Hidden family" in names


def test_an_unmeasured_reference_blocks(tmp_path):
    """Capture has nothing to normalise against, so those tasks would void."""
    import shutil

    shutil.copytree(CORPUS / "string-split", tmp_path / "pkg")
    manifest = tmp_path / "pkg" / "package.yaml"
    manifest.write_text(
        "\n".join(
            line
            for line in manifest.read_text().splitlines()
            if "s_ref_deterministic" not in line
        )
    )
    shutil.copytree(CORPUS / "token-count", tmp_path / "hidden")

    checks = check_corpus(tmp_path)
    blocking = [c for c in checks if c.status is Status.BLOCKING]
    assert any("Reference speedups" == c.name for c in blocking), [c.name for c in blocking]
    assert any("measure-reference" in c.remedy for c in blocking)


def test_an_unreachable_chain_is_blocking_not_skipped():
    """'We could not check' and 'the chain is fine' must not look the same."""
    from compilerforge.validator.cli import check_chain

    checks = check_chain(netuid=1, network="ws://127.0.0.1:1")
    assert checks[0].name == "Chain connection"
    assert checks[0].status is Status.BLOCKING


def test_every_status_renders():
    for status in Status:
        assert status.marker


@pytest.mark.parametrize("command", ["preflight", "hyperparameters"])
def test_the_cli_exposes_its_commands(command):
    """Both commands exist, render help, and take --netuid.

    Asserts against the command *definition* rather than the rendered help text.
    Rich wraps to the terminal width, so an 80-column CI runner splits long
    option names across lines and a substring check on the output passes locally
    and fails there — which is exactly what happened the first time this ran.
    """
    from typer.testing import CliRunner

    from compilerforge.validator.cli import app

    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output

    registered = {c.name or c.callback.__name__: c for c in app.registered_commands}
    assert command in registered, f"{command} is not registered; have {sorted(registered)}"

    params = {
        opt
        for name in registered[command].callback.__annotations__
        for opt in [name]
    }
    assert "netuid" in params, f"{command} does not take netuid; takes {sorted(params)}"
