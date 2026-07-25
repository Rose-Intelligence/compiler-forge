"""End-to-end evaluation against the real toolchain.

These build actual C code, run actual sanitizers and take actual Callgrind
measurements. They are the tests that would have caught every bug the unit tests
could not: a Callgrind cost line read from the wrong place, a cached baseline that
never got built, a differential harness pointed at the benchmark binary.

Marked ``slow`` and skipped when the toolchain is absent:

    pytest -m slow
    pytest -m "not slow"    # the default fast suite
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from compilerforge.corpus.package import LoadedPackage
from compilerforge.sdk.evaluator import LocalEvaluator

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "corpus"

pytestmark = pytest.mark.slow

_TOOLCHAIN = all(shutil.which(tool) for tool in ("clang", "cmake", "valgrind", "git"))
requires_toolchain = pytest.mark.skipif(
    not _TOOLCHAIN, reason="needs clang, cmake, valgrind and git"
)


@pytest.fixture(scope="module")
def evaluator(tmp_path_factory) -> LocalEvaluator:
    return LocalEvaluator(
        workdir=tmp_path_factory.mktemp("cf-integration"),
        corpus_snapshot="test",
        fuzz_seconds=1,
    )


@pytest.fixture(scope="module")
def string_split() -> LoadedPackage:
    return LoadedPackage.load(CORPUS / "string-split")


@pytest.fixture(scope="module")
def token_count() -> LoadedPackage:
    return LoadedPackage.load(CORPUS / "token-count")


@pytest.fixture(scope="module")
def matrix_stats() -> LoadedPackage:
    return LoadedPackage.load(CORPUS / "matrix-stats")


@requires_toolchain
def test_the_reference_patch_captures_exactly_its_own_reference(evaluator, string_split):
    """The definition of the capture scale, checked against itself.

    If this ever drifts from 1.0, either the stored reference speedup is stale or
    the measurement changed — and every score in the network moved with it.
    """
    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    result = evaluator.evaluate_patch(string_split.reference_patch.read_text(), task)

    assert result.voided_reason is None, result.voided_reason
    assert result.ok, result.summary()
    assert result.score.reference.capture == pytest.approx(1.0, abs=0.02)
    assert result.score.tier_a.deterministic_speedup > 1.0


@requires_toolchain
def test_every_gate_runs_and_passes_for_a_good_patch(evaluator, string_split):
    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    result = evaluator.evaluate_patch(string_split.reference_patch.read_text(), task)

    ran = {str(gate.name) for gate in result.score.gates}
    for expected in (
        "baseline_stable",
        "patch_hygiene",
        "patch_applies",
        "build",
        "test_inventory",
        "api_abi",
        "public_tests",
        "differential",
        "asan",
        "ubsan",
        "second_opt_level",
    ):
        assert expected in ran, f"{expected} never ran"
    assert all(gate.passed for gate in result.score.gates)


@requires_toolchain
def test_the_deterministic_tier_is_actually_deterministic(evaluator, string_split):
    """The property the whole consensus design rests on.

    Two independent evaluations of the same patch must report the same
    instruction count — not approximately, exactly.
    """
    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    patch = string_split.reference_patch.read_text()

    first = evaluator.evaluate_patch(patch, task)
    second = evaluator.evaluate_patch(patch, task)

    assert (
        first.score.tier_a.instructions_candidate
        == second.score.tier_a.instructions_candidate
    )
    assert (
        first.score.tier_a.deterministic_speedup
        == second.score.tier_a.deterministic_speedup
    )


@requires_toolchain
def test_a_patch_that_breaks_the_public_tests_is_rejected(evaluator, string_split, tmp_path):
    """Skipping the trim is faster and wrong. The project's own tests catch it."""
    source = (string_split.repo_dir / "src" / "csvsplit.c").read_text()
    broken = source.replace(
        "char *field = csv_trim(row.fields[i]);", "char *field = row.fields[i];"
    )
    assert broken != source

    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    result = evaluator.evaluate_patch(
        _make_patch(string_split.repo_dir, "src/csvsplit.c", broken, tmp_path), task
    )

    assert not result.ok
    assert str(result.score.failed_gate()) == "public_tests"


@requires_toolchain
def test_a_patch_that_only_breaks_on_hidden_inputs_is_still_rejected(
    evaluator, token_count, tmp_path
):
    """The case that justifies differential testing existing at all.

    An order-insensitive hash is a plausible optimization. It passes every public
    test, because none of them use anagrams. The hidden input generator emits them
    deliberately, so the differential stage catches what the test suite cannot.
    """
    optimized = (
        (token_count.root / "reference.patch").read_text()
    )
    assert optimized, "the reference patch is needed to build the broken variant"

    source = (token_count.repo_dir / "src" / "tokencount.c").read_text()
    broken = source.replace(
        "        hash = ((hash << 5) + hash) + (unsigned char)*p;",
        "        hash += (unsigned char)*p;  /* merges anagrams */",
    )
    assert broken != source

    task = evaluator.build_task(
        token_count, seed="0xfeed99", profile_name="small-vocabulary"
    )
    result = evaluator.evaluate_patch(
        _make_patch(token_count.repo_dir, "src/tokencount.c", broken, tmp_path), task
    )

    assert not result.ok
    gates = {str(g.name): g.passed for g in result.score.gates}
    assert gates.get("public_tests") is True, "the public suite does not cover anagrams"
    assert str(result.score.failed_gate()) == "differential"


@requires_toolchain
def test_a_patch_that_does_nothing_is_an_honest_null_not_a_failure(
    evaluator, string_split, tmp_path
):
    """Returning no improvement must pass every gate and score zero, not fail."""
    source = (string_split.repo_dir / "src" / "csvsplit.c").read_text()
    cosmetic = source.replace(
        "#include <ctype.h>", "#include <ctype.h>\n/* no functional change */"
    )
    assert cosmetic != source

    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    result = evaluator.evaluate_patch(
        _make_patch(string_split.repo_dir, "src/csvsplit.c", cosmetic, tmp_path), task
    )

    assert result.ok, result.summary()
    assert result.score.reference.capture == 0.0
    assert result.score.honest_null


@requires_toolchain
def test_a_patch_that_edits_a_test_file_never_reaches_measurement(
    evaluator, string_split, tmp_path
):
    task = evaluator.build_task(string_split, seed="0xabc123", profile_name="default")
    tests = (string_split.repo_dir / "tests" / "test_csvsplit.c").read_text()
    gutted = tests.replace("test_checksum_ignores_padding();", "")

    result = evaluator.evaluate_patch(
        _make_patch(string_split.repo_dir, "tests/test_csvsplit.c", gutted, tmp_path), task
    )

    assert not result.ok
    assert result.score.tier_a is None, "measurement must not run on a failed candidate"


@requires_toolchain
def test_capture_is_normalised_per_workload_profile(evaluator, token_count):
    """The same patch on two profiles should capture ~1.0 on both, even though
    the raw speedups differ several-fold."""
    patch = (token_count.root / "reference.patch").read_text()

    captures = []
    for profile in ("small-vocabulary", "medium-vocabulary"):
        task = evaluator.build_task(token_count, seed="0xfeed99", profile_name=profile)
        result = evaluator.evaluate_patch(patch, task)
        assert result.ok, f"{profile}: {result.summary()}"
        captures.append(result.score.reference.capture)

    for capture in captures:
        assert capture == pytest.approx(1.0, abs=0.05)


def _make_patch(repo: Path, relpath: str, content: str, tmp_path: Path) -> str:
    """Build a unified diff replacing one file's contents."""
    modified = tmp_path / "modified"
    if modified.exists():
        shutil.rmtree(modified)
    shutil.copytree(repo, modified)
    (modified / relpath).write_text(content)

    result = subprocess.run(
        [
            "diff",
            "-u",
            "--label",
            f"a/{relpath}",
            "--label",
            f"b/{relpath}",
            str(repo / relpath),
            str(modified / relpath),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


@requires_toolchain
def test_the_reference_agent_produces_a_real_improvement(evaluator, string_split):
    """The SDK's reference agent is a published control, so it has to work.

    It should find a genuine, gate-passing improvement — and capture well below
    1.0, because a control that matched the expert would leave no headroom for
    the competition it exists to calibrate.
    """
    agent = REPO_ROOT / "compilerforge" / "miner" / "reference_agent" / "agent.py"
    task = evaluator.build_task(string_split, seed="0x77", profile_name="default")

    result = evaluator.evaluate_agent(["python3", str(agent)], task)

    assert result.run is not None
    assert result.run.exit_code == 0
    assert not evaluator.interface_problems(result.run, task)
    assert result.run.produced_patch, "the reference agent found nothing to improve"

    assert result.ok, result.summary()
    assert result.score.tier_a.deterministic_speedup > 1.0
    assert 0.0 < result.score.reference.capture < 1.0


@requires_toolchain
def test_the_reference_agent_predicts_its_own_result(evaluator, string_split):
    """It measures with the same instrument the validator scores on, so its
    self-estimate should match what the validator independently measures.

    The estimate is never scored. It is checked here because a large gap would
    mean the agent is measuring something other than what it is paid for — which
    is the single most common way a miner wastes its budget.
    """
    agent = REPO_ROOT / "compilerforge" / "miner" / "reference_agent" / "agent.py"
    task = evaluator.build_task(string_split, seed="0x77", profile_name="default")

    result = evaluator.evaluate_agent(["python3", str(agent)], task)
    assert result.ok, result.summary()

    claimed = result.run.report.self_measurement.local_speedup_estimate
    measured = result.score.tier_a.deterministic_speedup
    assert claimed == pytest.approx(measured, rel=0.02)


@requires_toolchain
def test_the_build_uses_the_pinned_compiler(evaluator, string_split, tmp_path):
    """The toolchain digest must describe the compiler that actually ran.

    Nothing set CC for the first twenty-five commits, so CMake selected
    /usr/bin/cc — gcc on a stock Ubuntu host — while the digest was computed from
    clang. Two validators with the same clang and different gcc would produce
    different instruction counts and report the same digest, marking incomparable
    scores as comparable. That is precisely the failure the comparability tuple
    exists to prevent.
    """
    import shutil as _shutil

    from compilerforge.evaluation.build import (
        PINNED_C_COMPILER,
        Builder,
        Workspace,
    )

    task = evaluator.build_task(string_split, seed="0xcc", profile_name="default")
    workspace = Workspace.create(string_split.repo_dir, tmp_path / "pinned")

    assert Builder().build(workspace, task.task, opt_level="-O2").ok

    expected = Path(_shutil.which(PINNED_C_COMPILER)).resolve()
    caches = list(workspace.root.rglob("CMakeCache.txt"))
    assert caches, "the build produced no CMake cache to inspect"

    for cache in caches:
        for line in cache.read_text().splitlines():
            if line.startswith("CMAKE_C_COMPILER:"):
                used = Path(line.split("=", 1)[1].strip()).resolve()
                assert used == expected, (
                    f"build used {used}, but the toolchain digest is computed "
                    f"from {expected}"
                )


@requires_toolchain
def test_a_build_with_the_wrong_compiler_is_rejected(evaluator, string_split, tmp_path):
    """The guard must fail closed, not warn.

    The mismatch is constructed through the contract, which is the only way it
    could occur in practice: a task pinning one compiler, and a cache recording
    another.
    """
    import shutil as _shutil

    from compilerforge.evaluation.build import Builder, ToolchainMismatch, Workspace

    if not _shutil.which("gcc"):
        pytest.skip("needs a second compiler to create the mismatch")

    selected = evaluator.build_task(string_split, seed="0xcc", profile_name="default")
    gcc_task = selected.task.model_copy(
        update={
            "build": selected.task.build.model_copy(
                update={"c_compiler": "gcc", "cxx_compiler": "g++"}
            )
        }
    )

    workspace = Workspace.create(string_split.repo_dir, tmp_path / "mismatch")
    assert Builder().build(workspace, gcc_task, opt_level="-O2").ok

    # The cache now records gcc. Checking it against the clang pin must raise.
    with pytest.raises(ToolchainMismatch, match="toolchain digest"):
        Builder()._assert_pinned_compiler_was_used(workspace, "clang")


@requires_toolchain
def test_a_patch_spanning_several_source_files_is_evaluated_whole(
    evaluator, matrix_stats
):
    """A candidate may restructure across translation units.

    Both single-file packages happen to have one source file each, so for a long
    time nothing exercised the multi-file path even though the patch scope is a
    glob and the changed-file cap is 25. This package spreads the inefficiency
    across three translation units deliberately: element access in matrix.c, the
    redundant traversals in vector.c, and the column gather and per-element
    Newton iteration in stats.c.

    A candidate that rewrites only one of them leaves most of the cost behind, so
    this asserts the whole patch is applied and measured as one unit.
    """
    task = evaluator.build_task(matrix_stats, seed="0xmulti")
    patch = (CORPUS / "matrix-stats" / "reference.patch").read_text()

    result = evaluator.evaluate_patch(patch, task)

    assert result.score is not None, result.voided_reason
    assert result.score.all_gates_passed(), result.summary()

    hygiene = next(
        g for g in result.score.gates if str(g.name) == "patch_hygiene"
    )
    assert hygiene.detail.startswith("3 files"), hygiene.detail

    touched = {
        line[6:].strip()
        for line in patch.splitlines()
        if line.startswith("--- a/")
    }
    assert touched == {"src/matrix.c", "src/vector.c", "src/stats.c"}, touched

    # The improvement has to come from the combination. Each file alone is a
    # fraction of it, so a materially lower speedup would mean only part of the
    # patch took effect.
    assert result.score.tier_a is not None
    assert result.score.tier_a.deterministic_speedup > 2.0


@requires_toolchain
def test_a_patch_that_edits_a_public_header_is_rejected(evaluator, matrix_stats):
    """include/** is patchable, but the ABI gate still owns the public surface.

    A candidate may add an internal header; it may not change a declaration
    callers outside the package compile against.
    """
    task = evaluator.build_task(matrix_stats, seed="0xheader")
    patch = (
        "--- a/include/mstats.h\n"
        "+++ b/include/mstats.h\n"
        "@@ -20,7 +20,7 @@\n"
        " /* Allocation. Returns NULL on failure; ms_matrix_free tolerates NULL. */\n"
        " ms_matrix *ms_matrix_alloc(size_t rows, size_t cols);\n"
        "-void ms_matrix_free(ms_matrix *m);\n"
        "+void ms_matrix_free(ms_matrix *m, int unused);\n"
        " \n"
        " /* Element access. Out-of-range indices are undefined behaviour by contract. */\n"
        " double ms_at(const ms_matrix *m, size_t row, size_t col);\n"
    )

    result = evaluator.evaluate_patch(patch, task)

    assert result.score is None or not result.score.all_gates_passed()
