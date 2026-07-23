"""Task selection must be a pure function of public inputs.

If two validators drawing the same block hash derive different tasks, nothing
downstream can be reconciled — they are measuring different programs and calling
the result the same name. These tests exist to keep that from happening quietly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from compilerforge.corpus.package import Corpus, LoadedPackage, TaskPackage, WorkloadProfile
from compilerforge.evaluation.selection import (
    RoundSeed,
    SelectionError,
    _sample_without_replacement,
    derive_round,
)
from compilerforge.protocol.task import EquivalenceDiscipline, WorkloadFamily
from compilerforge.spec import SPEC

TOOLCHAIN = "sha256:" + "1" * 64

#: Held for the whole module; the packages are read-only fixtures.
_ROOTS = tempfile.TemporaryDirectory(prefix="cf-selection-tests-")


def _package(package_id: str, *, hidden: bool = False, family=WorkloadFamily.PARSING):
    pkg = TaskPackage(
        package_id=package_id,
        family=family,
        license="MIT",
        revision="0" * 40,
        hidden_family=hidden,
        build_command="true",
        test_command="true",
        benchmark_command="true",
        equivalence_discipline=EquivalenceDiscipline.BYTE_EQUAL,
        workload_profiles=(
            WorkloadProfile(name="small", selection_weight=1.0, s_ref_deterministic=1.5),
            WorkloadProfile(name="large", selection_weight=1.0, s_ref_deterministic=2.0),
        ),
    )
    # A real directory: build_task hashes the test inventory off disk, so a
    # fixture without one would exercise a different code path than production.
    root = Path(_ROOTS.name) / package_id
    (root / "repo" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "repo" / "tests" / "test_main.c").write_text(f"/* {package_id} */\n")
    return LoadedPackage(
        root=root, package=pkg, content_hash="sha256:" + package_id.ljust(64, "0")[:64]
    )


def _corpus(public: int = 6, hidden: int = 2, snapshot: str = "snap-1") -> Corpus:
    packages = {}
    for i in range(public):
        p = _package(f"public-{i}")
        packages[p.package.package_id] = p
    for i in range(hidden):
        p = _package(f"hidden-{i}", hidden=True)
        packages[p.package.package_id] = p
    return Corpus(snapshot_id=snapshot, packages=packages)


def _seed(block_hash: str = "0x" + "ab" * 32, snapshot: str = "snap-1") -> RoundSeed:
    return RoundSeed(
        block_number=1000,
        block_hash=block_hash,
        corpus_snapshot=snapshot,
        spec_digest=SPEC.digest(),
    )


def test_the_same_block_hash_derives_an_identical_round():
    corpus = _corpus()
    a = derive_round(seed=_seed(), corpus=corpus, toolchain_digest=TOOLCHAIN)
    b = derive_round(seed=_seed(), corpus=corpus, toolchain_digest=TOOLCHAIN)

    assert a.manifest_hash() == b.manifest_hash()
    assert [t.task.task_id for t in a.tasks] == [t.task.task_id for t in b.tasks]
    assert [t.task.seed for t in a.tasks] == [t.task.seed for t in b.tasks]
    assert [t.profile.name for t in a.tasks] == [t.profile.name for t in b.tasks]


def test_a_different_block_hash_derives_a_different_round():
    corpus = _corpus(public=12, hidden=4)
    a = derive_round(seed=_seed("0x" + "ab" * 32), corpus=corpus, toolchain_digest=TOOLCHAIN)
    b = derive_round(seed=_seed("0x" + "cd" * 32), corpus=corpus, toolchain_digest=TOOLCHAIN)
    assert a.manifest_hash() != b.manifest_hash()


def test_task_ids_are_unique_within_a_round():
    plan = derive_round(seed=_seed(), corpus=_corpus(), toolchain_digest=TOOLCHAIN)
    ids = [t.task.task_id for t in plan.tasks]
    assert len(ids) == len(set(ids))


def test_no_package_is_drawn_twice():
    """One repository appearing twice would let a single family carry an artifact."""
    plan = derive_round(
        seed=_seed(), corpus=_corpus(public=6, hidden=2), toolchain_digest=TOOLCHAIN
    )
    public_ids = [t.package_id for t in plan.public_tasks]
    assert len(public_ids) == len(set(public_ids))


def test_a_round_always_includes_a_hidden_family():
    plan = derive_round(seed=_seed(), corpus=_corpus(), toolchain_digest=TOOLCHAIN)
    assert len(plan.hidden_tasks) >= 1


def test_a_corpus_with_no_hidden_family_cannot_produce_a_round():
    with pytest.raises(SelectionError, match="hidden"):
        derive_round(
            seed=_seed(), corpus=_corpus(public=4, hidden=0), toolchain_digest=TOOLCHAIN
        )


def test_a_corpus_snapshot_mismatch_is_refused():
    with pytest.raises(SelectionError, match="corpus"):
        derive_round(
            seed=_seed(snapshot="snap-other"),
            corpus=_corpus(snapshot="snap-1"),
            toolchain_digest=TOOLCHAIN,
        )


def test_a_consensus_spec_mismatch_is_refused():
    """Scores from different consensus regimes are never comparable."""
    stale = RoundSeed(
        block_number=1,
        block_hash="0x" + "11" * 32,
        corpus_snapshot="snap-1",
        spec_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(SelectionError, match="consensus spec"):
        derive_round(seed=stale, corpus=_corpus(), toolchain_digest=TOOLCHAIN)


def test_the_published_manifest_hides_which_families_are_held_out():
    plan = derive_round(seed=_seed(), corpus=_corpus(), toolchain_digest=TOOLCHAIN)
    manifest = plan.manifest()
    assert "hidden_task_count" in manifest
    serialised = str(manifest)
    for task in plan.hidden_tasks:
        assert task.package_id not in serialised


def test_labelled_streams_are_independent():
    """Task selection must not shift because some other draw happened first."""
    seed = _seed()
    assert seed.stream("public-packages") != seed.stream("hidden-packages")
    assert seed.stream("producer|x") == seed.stream("producer|x")


def test_sampling_does_not_depend_on_the_python_random_stream():
    """Written out longhand so a Python upgrade cannot change the task set."""
    items = list(range(20))
    first = _sample_without_replacement(items, 5, 12345)
    second = _sample_without_replacement(items, 5, 12345)
    assert first == second
    assert len(set(first)) == 5
    assert _sample_without_replacement(items, 5, 54321) != first


def test_sampling_handles_a_request_larger_than_the_pool():
    assert len(_sample_without_replacement([1, 2, 3], 10, 7)) == 3


def test_profile_selection_is_reproducible_from_an_integer():
    package = _package("p")
    assert package.select_profile(12345).name == package.select_profile(12345).name


def test_corpus_manifest_omits_hidden_packages():
    corpus = _corpus(public=3, hidden=2)
    manifest = corpus.manifest()
    assert len(manifest["public_packages"]) == 3
    assert manifest["hidden_package_count"] == 2
    assert "hidden-0" not in str(manifest["public_packages"])
