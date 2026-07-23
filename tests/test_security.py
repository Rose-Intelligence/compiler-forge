"""The security boundary and the anti-gaming rules.

A validator holds a hotkey on the same machine that runs anonymous, arbitrary
code. Every assertion here corresponds to a way that arrangement could go wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from compilerforge.evaluation.build import check_patch_hygiene, parse_patch
from compilerforge.protocol.commitment import MAX_COMMITMENT_BYTES, ArtifactCommitment
from compilerforge.protocol.task import (
    BenchmarkContract,
    BuildContract,
    EquivalenceContract,
    EquivalenceDiscipline,
    RepositoryContract,
    ResourceContract,
    Task,
    TestContract,
)
from compilerforge.sandbox.isolation import (
    IsolationError,
    IsolationProfile,
    NetworkMode,
    Phase,
    Runtime,
)
from compilerforge.spec import SPEC


def _task(**overrides) -> Task:
    base = dict(
        task_id="sha256:" + "0" * 64,
        repository=RepositoryContract(
            uri="mounted:///workspace/repo", revision="0" * 40, license="MIT"
        ),
        build=BuildContract(command="true", toolchain_digest="sha256:" + "1" * 64),
        tests=TestContract(public_command="true", inventory_hash="sha256:" + "2" * 64),
        equivalence=EquivalenceContract(discipline=EquivalenceDiscipline.BYTE_EQUAL),
        benchmark=BenchmarkContract(command="true"),
        seed="0xdead",
    )
    base.update(overrides)
    return Task(**base)


def _profile(**overrides) -> IsolationProfile:
    base = dict(
        runtime=Runtime.RUNSC,
        phase=Phase.OPTIMIZE,
        network=NetworkMode.NONE,
        resources=ResourceContract(),
        wall_seconds=600,
    )
    base.update(overrides)
    return IsolationProfile(**base)


# ---------------------------------------------------------------------------
# network isolation
# ---------------------------------------------------------------------------


def test_a_task_cannot_hand_the_agent_a_remote_repository():
    """A remote URI defeats the entire network-isolation argument."""
    with pytest.raises(ValueError, match="mounted://"):
        RepositoryContract(uri="https://github.com/x/y", revision="0" * 40, license="MIT")


def test_package_mirrors_are_not_available_during_an_authoritative_run():
    with pytest.raises(IsolationError, match="preparation-phase"):
        _profile(network=NetworkMode.MIRROR).assert_safe()


def test_mirrors_remain_available_during_preparation():
    _profile(phase=Phase.PREPARE, network=NetworkMode.MIRROR).assert_safe()


def test_no_network_produces_the_isolating_container_flag():
    assert "--network=none" in _profile().container_args()


# ---------------------------------------------------------------------------
# sandbox hardening
# ---------------------------------------------------------------------------


def test_a_shared_kernel_runtime_is_refused_for_authoritative_phases():
    with pytest.raises(IsolationError, match="shares the host kernel"):
        _profile(runtime=Runtime.RUNC).assert_safe()


def test_hardened_runtimes_are_accepted():
    for runtime in (Runtime.RUNSC, Runtime.KATA, Runtime.FIRECRACKER):
        _profile(runtime=runtime).assert_safe()
        assert runtime.is_hardened


def test_the_core_hardening_flags_cannot_be_disabled():
    for field in ("read_only_rootfs", "drop_all_capabilities", "no_new_privileges"):
        with pytest.raises(IsolationError, match=field):
            _profile(**{field: False}).assert_safe()


def test_artifacts_never_run_as_root():
    with pytest.raises(IsolationError, match="root"):
        _profile(run_as_user="0:0").assert_safe()


def test_resource_limits_reach_the_container_arguments():
    args = _profile().container_args()
    joined = " ".join(args)
    assert "--pids-limit=" in joined  # fork bombs
    assert "--memory=" in joined
    assert "--cpus=" in joined
    assert "--ulimit=fsize=" in joined  # disk exhaustion
    # A memory cap escapable into swap is not a cap.
    assert "--memory-swap=" in joined


def test_the_docker_socket_can_never_be_mounted():
    """Mounting it would hand an artifact the host's container daemon."""
    profile = _profile()
    with pytest.raises(IsolationError, match="refusing to mount"):
        profile.assert_mounts_safe({"/var/run/docker.sock": "/var/run/docker.sock"})


def test_wallet_material_can_never_be_mounted():
    profile = _profile()
    with pytest.raises(IsolationError, match="refusing to mount"):
        profile.assert_mounts_safe({"/root/.bittensor": "/keys"})
    with pytest.raises(IsolationError, match="refusing to mount"):
        profile.assert_mounts_safe({"/root/.bittensor/wallets": "/keys"})


def test_ordinary_mounts_are_permitted():
    _profile().assert_mounts_safe(
        {"/srv/work/repo": "/workspace/repo", "/srv/work/out": "/output"}
    )


# ---------------------------------------------------------------------------
# artifact commitments
# ---------------------------------------------------------------------------


def test_a_commitment_must_carry_a_content_digest():
    with pytest.raises(ValueError, match="sha256"):
        ArtifactCommitment(image="ghcr.io/x/y", digest="latest")


def test_the_image_field_cannot_smuggle_its_own_digest():
    with pytest.raises(ValueError, match="bare repository"):
        ArtifactCommitment(image="ghcr.io/x/y@sha256:" + "a" * 64, digest="sha256:" + "a" * 64)


def test_a_commitment_is_only_ever_pulled_by_digest():
    commitment = ArtifactCommitment(image="ghcr.io/x/y", digest="sha256:" + "a" * 64)
    assert commitment.pull_reference() == "ghcr.io/x/y@sha256:" + "a" * 64
    assert "@sha256:" in commitment.pull_reference()


def test_commitments_round_trip_through_the_chain_encoding():
    original = ArtifactCommitment(
        image="ghcr.io/x/y",
        digest="sha256:" + "b" * 64,
        agent_version="2.1.0",
        cells=("generalist", "compression"),
        committed_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    restored = ArtifactCommitment.decode(original.encode())
    assert restored == original


def test_an_oversized_commitment_is_refused():
    oversized = ArtifactCommitment(
        image="ghcr.io/x/" + "y" * 600, digest="sha256:" + "c" * 64
    )
    with pytest.raises(ValueError, match="limit"):
        oversized.encode()
    assert len(ArtifactCommitment(
        image="ghcr.io/x/y", digest="sha256:" + "c" * 64
    ).encode().encode()) < MAX_COMMITMENT_BYTES


# ---------------------------------------------------------------------------
# patch hygiene
# ---------------------------------------------------------------------------


def _diff(*paths: str, added: int = 5) -> str:
    out = []
    for path in paths:
        out.append(f"--- a/{path}")
        out.append(f"+++ b/{path}")
        out.append("@@ -1,1 +1,2 @@")
        out.extend(f"+line {i}" for i in range(added))
    return "\n".join(out) + "\n"


def test_editing_the_build_definition_is_measurement_manipulation():
    """Changing the flags you are measured under is not an optimization."""
    result = check_patch_hygiene(_diff("CMakeLists.txt"), _task())
    assert not result.passed
    assert "build definition" in result.detail


def test_editing_a_test_file_is_caught_by_hygiene():
    result = check_patch_hygiene(_diff("tests/test_main.c"), _task())
    assert not result.passed
    assert "test file" in result.detail


def test_an_ordinary_source_patch_passes_hygiene():
    assert check_patch_hygiene(_diff("src/parser.c"), _task()).passed


def test_a_sprawling_patch_is_rejected():
    many = _diff(*[f"src/file{i}.c" for i in range(SPEC.budget.max_patch_changed_files + 5)])
    result = check_patch_hygiene(many, _task())
    assert not result.passed
    assert "files" in result.detail


def test_an_enormous_patch_is_rejected():
    huge = _diff("src/main.c", added=SPEC.budget.max_patch_added_lines + 100)
    result = check_patch_hygiene(huge, _task())
    assert not result.passed
    assert "lines" in result.detail


def test_patch_parsing_counts_files_and_lines():
    stats = parse_patch(_diff("src/a.c", "src/b.c", added=3))
    assert stats.touched_count == 2
    assert stats.added_lines == 6


# ---------------------------------------------------------------------------
# consensus comparability
# ---------------------------------------------------------------------------


def test_scores_from_different_regimes_are_not_comparable():
    from compilerforge.protocol.score import ScoreArtifact

    a = ScoreArtifact(
        artifact_digest="sha256:" + "a" * 64,
        task_id="t",
        toolchain_digest="sha256:" + "1" * 64,
        corpus_snapshot="snap-1",
    )
    b = ScoreArtifact(
        artifact_digest="sha256:" + "a" * 64,
        task_id="t",
        toolchain_digest="sha256:" + "2" * 64,  # a different compiler
        corpus_snapshot="snap-1",
    )
    assert not a.comparable_with(b)
    assert a.comparable_with(a)


def test_a_signature_covers_everything_except_itself():
    from compilerforge.protocol.score import ScoreArtifact

    artifact = ScoreArtifact(
        artifact_digest="sha256:" + "a" * 64,
        task_id="t",
        toolchain_digest="sha256:" + "1" * 64,
        corpus_snapshot="snap-1",
    )
    before = artifact.content_hash()
    artifact.validator_signature = "0xdeadbeef"
    assert artifact.content_hash() == before

    artifact.score = 0.9
    assert artifact.content_hash() != before


def test_the_consensus_digest_changes_when_any_constant_changes():
    from compilerforge.spec import ConsensusSpec, DethronementSpec

    default = ConsensusSpec()
    altered = ConsensusSpec(dethronement=DethronementSpec(margin=0.10))
    assert default.digest() != altered.digest()


def test_score_component_weights_must_sum_to_one():
    from compilerforge.spec import ConsensusSpec, ScoreComponentWeights

    with pytest.raises(ValueError, match="sum to 1.0"):
        ConsensusSpec(components=ScoreComponentWeights(deterministic_capture=0.9))


def test_the_mechanism_split_must_use_the_full_u16_range():
    from compilerforge.spec import ConsensusSpec, EmissionSpec

    with pytest.raises(ValueError, match="65535"):
        ConsensusSpec(emission=EmissionSpec(mechanism_split_u16=(100, 200)))
