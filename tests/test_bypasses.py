"""Ways a miner could score without optimizing anything.

Every test here corresponds to an attack that worked, or a denial of service that
worked, against an earlier version of this code. They are written as attacks
rather than as feature tests because that is how they were found.

The rule they enforce: a patch may change the program under optimization, and
nothing else. Not what is measured, not what verifies it, not how it is built.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from compilerforge.corpus.package import LoadedPackage, immutable_tree_hash
from compilerforge.evaluation.build import (
    Workspace,
    check_immutable_tree,
    check_patch_hygiene,
    toolchain_digest,
)
from compilerforge.evaluation.pipeline import CandidateUnmeasurable, TaskVoided

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "corpus"


@pytest.fixture(scope="module")
def package() -> LoadedPackage:
    return LoadedPackage.load(CORPUS / "string-split")


@pytest.fixture(scope="module")
def task(package: LoadedPackage):
    return package.build_task(
        task_id="sha256:" + "0" * 64,
        seed="0xbad",
        toolchain_digest=toolchain_digest(),
        profile=package.package.workload_profiles[0],
    )


def _diff(original: Path, modified: Path, relpath: str) -> str:
    result = subprocess.run(
        [
            "diff", "-u",
            "--label", f"a/{relpath}",
            "--label", f"b/{relpath}",
            str(original / relpath),
            str(modified / relpath),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# moving the measurement boundary
# ---------------------------------------------------------------------------


def test_a_patch_cannot_touch_the_benchmark(package, task, tmp_path):
    """The attack that scored the maximum possible capture while optimizing nothing.

    Moving `CF_BENCH_STOP` above the work leaves the program's output completely
    unchanged — every correctness gate passes — while the measured region does
    almost nothing. On the earlier denylist-based hygiene check this produced a
    3.09x "speedup" and capture 2.000, the maximum, from a patch that changed no
    library code at all.
    """
    modified = tmp_path / "tree"
    shutil.copytree(package.repo_dir, modified)

    bench = modified / "bench" / "bench.c"
    source = bench.read_text()
    bench.write_text(
        source.replace(
            "    CF_BENCH_START;\n    for (size_t r = 0; r < repeats; r++) {",
            "    CF_BENCH_START;\n    CF_BENCH_STOP;\n    for (size_t r = 0; r < repeats; r++) {",
        )
    )
    assert bench.read_text() != source, "the attack patch did not apply"

    result = check_patch_hygiene(
        _diff(package.repo_dir, modified, "bench/bench.c"), task
    )
    assert not result.passed
    assert "outside the patchable area" in result.detail


def test_a_patch_cannot_touch_the_differential_harness(package, task, tmp_path):
    """It decides whether the patch preserved behaviour. Obviously not the miner's."""
    modified = tmp_path / "tree"
    shutil.copytree(package.repo_dir, modified)
    harness = modified / "tests" / "differential.c"
    harness.write_text(harness.read_text().replace("csv_trim(", "(char*)("))

    result = check_patch_hygiene(
        _diff(package.repo_dir, modified, "tests/differential.c"), task
    )
    assert not result.passed


def test_a_patch_cannot_touch_the_build_definition(package, task, tmp_path):
    """Changing the flags you are measured under is not an optimization."""
    modified = tmp_path / "tree"
    shutil.copytree(package.repo_dir, modified)
    cml = modified / "CMakeLists.txt"
    cml.write_text(cml.read_text() + '\nadd_compile_options("-Ofast")\n')

    result = check_patch_hygiene(_diff(package.repo_dir, modified, "CMakeLists.txt"), task)
    assert not result.passed


def test_the_allowlist_permits_the_program_under_optimization(package, task, tmp_path):
    """The complement: a legitimate patch must not be blocked."""
    modified = tmp_path / "tree"
    shutil.copytree(package.repo_dir, modified)
    source = modified / "src" / "csvsplit.c"
    source.write_text(source.read_text().replace("size_t count = 1;", "size_t count = 1; /* x */"))

    result = check_patch_hygiene(_diff(package.repo_dir, modified, "src/csvsplit.c"), task)
    assert result.passed, result.detail


# ---------------------------------------------------------------------------
# the second layer: the tree, not the diff
# ---------------------------------------------------------------------------


def test_a_file_added_outside_the_scope_is_caught_by_the_tree_hash(package, task, tmp_path):
    """The hygiene gate reads the diff; this reads what actually resulted.

    A patch can add or delete files in ways a diff-shaped check may not classify
    correctly, and the measurement runs against the tree rather than the diff.
    """
    workspace = Workspace.create(package.repo_dir, tmp_path / "ws")
    (workspace.root / "cmake").mkdir(exist_ok=True)
    (workspace.root / "cmake" / "Injected.cmake").write_text("# injected\n")

    result = check_immutable_tree(workspace, task)
    assert not result.passed
    assert "added, removed or modified" in result.detail


def test_a_file_deleted_outside_the_scope_is_caught(package, task, tmp_path):
    workspace = Workspace.create(package.repo_dir, tmp_path / "ws")
    (workspace.root / "tests" / "differential.c").unlink()

    assert not check_immutable_tree(workspace, task).passed


def test_an_untouched_tree_passes(package, task, tmp_path):
    workspace = Workspace.create(package.repo_dir, tmp_path / "ws")
    assert check_immutable_tree(workspace, task).passed


def test_changes_inside_the_scope_do_not_trip_the_tree_hash(package, task, tmp_path):
    workspace = Workspace.create(package.repo_dir, tmp_path / "ws")
    source = workspace.root / "src" / "csvsplit.c"
    source.write_text(source.read_text() + "\n/* a legitimate edit */\n")

    assert check_immutable_tree(workspace, task).passed


def test_a_task_without_an_immutable_hash_refuses_to_measure(package, task, tmp_path):
    """Fail closed. An unverifiable tree must not be scored."""
    workspace = Workspace.create(package.repo_dir, tmp_path / "ws")
    unverifiable = task.model_copy(
        update={"patch_scope": task.patch_scope.model_copy(update={"immutable_hash": ""})}
    )

    result = check_immutable_tree(workspace, unverifiable)
    assert not result.passed
    assert "refusing to measure" in result.detail


def test_the_hash_covers_everything_outside_the_patchable_area(package):
    """Sanity check on the hash itself, independent of the gate."""
    patchable = package.package.patchable_paths
    baseline = immutable_tree_hash(package.repo_dir, patchable)
    assert baseline.startswith("sha256:")
    # Same inputs, same hash — two validators must agree on what is immutable.
    assert immutable_tree_hash(package.repo_dir, patchable) == baseline


# ---------------------------------------------------------------------------
# denial of service
# ---------------------------------------------------------------------------


def test_an_unmeasurable_candidate_is_not_a_task_void():
    """A miner must not be able to kill a task for everyone.

    Voiding a task removes it from every miner's score *and* prevents a crown
    change that round, so conflating "this patch cannot be measured" with "this
    task is broken" hands any miner both a denial of service and a way for an
    incumbent to defend itself indefinitely.
    """
    assert not issubclass(CandidateUnmeasurable, TaskVoided)
    assert not issubclass(TaskVoided, CandidateUnmeasurable)


def test_the_pipeline_attributes_measurement_failures_to_the_right_side():
    """Baseline failure voids; candidate failure scores zero."""
    import inspect

    from compilerforge.evaluation.pipeline import Evaluator

    source = inspect.getsource(Evaluator.evaluate)
    assert "except CandidateUnmeasurable" in source
    assert "Tier A baseline measurement failed" in source

    measure = inspect.getsource(Evaluator._measure_tier_a)
    assert "CandidateUnmeasurable" in measure
    assert "not deterministic" in measure
