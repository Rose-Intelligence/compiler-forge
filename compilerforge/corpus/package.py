"""Task packages and the corpus manifest.

A task package is a directory containing everything needed to evaluate one
repository revision against one workload, plus the reference optimization that
defines the 1.0 point on the capture scale.

    <package>/
      package.yaml            declarative contract (see TaskPackage below)
      repo/                   the pinned source tree, or a fetch manifest
      inputs/public/          published input corpus
      inputs/hidden.sealed    drand-timelocked hidden inputs
      reference.patch         the human expert commit, or a curated equivalent
      fuzz/                   libFuzzer targets
      comparator.py           optional package-specific comparator override

Package identity is the content hash of every file in it. Two validators disagreeing
about which bytes they evaluated is not a scoring problem that can be fixed later,
so the hash is computed over sorted (relpath, sha256) pairs and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from compilerforge.protocol.task import (
    BenchmarkContract,
    BuildContract,
    EquivalenceContract,
    EquivalenceDiscipline,
    Objective,
    PatchScope,
    RepositoryContract,
    ResourceContract,
    Task,
    TestContract,
    WorkloadFamily,
)
from compilerforge.spec import SPEC

#: Files that never contribute to package identity.
_IGNORED = {".git", "__pycache__", ".pytest_cache", "build", ".DS_Store"}


def _iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if any(part in _IGNORED for part in p.relative_to(root).parts):
            continue
        if p.is_file():
            out.append(p)
    return out


def content_hash_tree(root: Path) -> str:
    """Hash a directory as sorted (relpath, sha256) pairs."""
    entries = []
    for p in _iter_files(root):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        entries.append((str(p.relative_to(root)), h))
    payload = json.dumps(entries, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def inventory_hash(root: Path, patterns: tuple[str, ...]) -> str:
    """Hash the test inventory.

    Verified against, rather than a file count, so a miner cannot rename a test
    file into oblivion and pass a counting check.
    """
    entries = []
    for pattern in sorted(patterns):
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                entries.append(
                    (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
                )
    entries.sort()
    payload = json.dumps(entries, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def immutable_tree_hash(root: Path, patchable: tuple[str, ...]) -> str:
    """Hash every file a patch is *not* allowed to touch.

    Comparing this before and after a patch applies catches modifications,
    additions and deletions anywhere outside the patchable area — including
    tricks a pattern check on the diff itself would miss, such as adding a file
    that shadows a validator-owned one.
    """
    entries = []
    for path in _iter_files(root):
        relative = path.relative_to(root)
        if any(relative.match(pattern) for pattern in patchable):
            continue
        entries.append(
            (str(relative), hashlib.sha256(path.read_bytes()).hexdigest())
        )
    entries.sort()
    payload = json.dumps(entries, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class WorkloadProfile(BaseModel):
    """One selectable shape of the benchmark workload.

    Hidden workload sizes are a first-class anti-overfitting device:
    the published defaults are not the ones a round necessarily uses.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    args: tuple[str, ...] = ()
    published: bool = True
    #: Relative weight in the block-hash-driven selection.
    selection_weight: float = 1.0
    #: The reference speedup *on this profile*, measured once at corpus build
    #: time and frozen.
    #:
    #: This is per-profile rather than per-package because the same reference
    #: patch can be worth wildly different amounts on different workload shapes —
    #: an algorithmic fix that is 4x on a small working set can be 10x on a large
    #: one. Normalising capture against a package-wide average would silently
    #: over-reward artifacts on the easy profiles and under-reward them on the
    #: hard ones.
    s_ref_deterministic: float | None = None


class ReferenceOptimization(BaseModel):
    """The human expert commit that defines S_ref for this task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patch_path: str = "reference.patch"
    #: Provenance matters: an artifact that beats a curated patch has proved
    #: something different from one that beats a memorisable upstream commit.
    origin: str = "curated"
    upstream_commit: str | None = None
    #: Fallback used only by profiles that declare no reference of their own.
    #: Prefer the per-profile value; a package whose profiles differ materially
    #: in difficulty should not rely on this.
    s_ref_deterministic: float | None = None

    @field_validator("origin")
    @classmethod
    def _known_origin(cls, v: str) -> str:
        if v not in {"upstream_commit", "curated"}:
            raise ValueError("reference origin must be 'upstream_commit' or 'curated'")
        return v


class TaskPackage(BaseModel):
    """``package.yaml`` — the declarative contract for one corpus entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str
    family: WorkloadFamily
    license: str
    revision: str
    #: Held out of every published corpus listing. A hidden package
    #: never appears in the manifest a miner can read.
    hidden_family: bool = False

    build_command: str
    forbidden_flags: tuple[str, ...] = ()
    #: The pinned toolchain. Overridable per package for a future track that
    #: needs a different compiler, but V1 is one pinned LLVM/Clang.
    c_compiler: str = "clang"
    cxx_compiler: str = "clang++"
    #: Paths a patch may touch. Everything else — tests, benchmark drivers, the
    #: build definition, fuzz targets — belongs to the validator.
    patchable_paths: tuple[str, ...] = ("src/**", "include/**")
    test_command: str
    test_inventory_globs: tuple[str, ...] = ("tests/**/*",)

    benchmark_command: str
    objective: Objective = Objective.BALANCED
    measured_region: str = "cf_bench_start/cf_bench_stop"
    workload_profiles: tuple[WorkloadProfile, ...] = ()

    #: Reads one hidden input on stdin and prints every declared observable.
    #: A package without one can only be differentially tested through its
    #: benchmark, which is far weaker evidence.
    differential_command: str | None = None

    equivalence_discipline: EquivalenceDiscipline
    float_tolerance_ulp: int | None = None
    relative_error_budget: float | None = None
    side_effects: tuple[str, ...] = ("stdout", "exit_code")

    reference: ReferenceOptimization = Field(default_factory=ReferenceOptimization)
    resources: ResourceContract = Field(default_factory=ResourceContract)

    fuzz_targets: tuple[str, ...] = ()
    #: Differential input generator, invoked as `<cmd> --seed N --count M --out DIR`.
    input_generator: str | None = None

    @field_validator("workload_profiles")
    @classmethod
    def _at_least_one_profile(cls, v: tuple[WorkloadProfile, ...]) -> tuple[WorkloadProfile, ...]:
        if not v:
            raise ValueError("a task package must declare at least one workload profile")
        return v

    @classmethod
    def load(cls, path: Path) -> TaskPackage:
        data: dict[str, Any] = yaml.safe_load(path.read_text())
        return cls.model_validate(data)


@dataclass(frozen=True, slots=True)
class LoadedPackage:
    """A task package on disk, with its identity computed."""

    root: Path
    package: TaskPackage
    content_hash: str

    @classmethod
    def load(cls, root: Path) -> LoadedPackage:
        manifest = root / "package.yaml"
        if not manifest.exists():
            raise FileNotFoundError(f"no package.yaml in {root}")
        pkg = TaskPackage.load(manifest)
        return cls(root=root, package=pkg, content_hash=content_hash_tree(root))

    @property
    def repo_dir(self) -> Path:
        return self.root / "repo"

    @property
    def reference_patch(self) -> Path:
        return self.root / self.package.reference.patch_path

    def reference_speedup(self, profile: WorkloadProfile) -> float | None:
        """The reference speedup to normalise capture against for one profile.

        Falls back to the package-level value only when the profile declares
        none. Returns ``None`` when neither exists, which makes the task
        unscoreable — capture has nothing to normalise against, and the caller
        must void rather than invent a denominator.
        """
        if profile.s_ref_deterministic is not None:
            return profile.s_ref_deterministic
        return self.package.reference.s_ref_deterministic

    def select_profile(self, rng_value: int) -> WorkloadProfile:
        """Pick a workload profile deterministically from round entropy.

        The selection is weighted, so a package can make its unusual shapes rare
        without making them impossible.
        """
        profiles = self.package.workload_profiles
        total = sum(p.selection_weight for p in profiles)
        if total <= 0:
            return profiles[0]
        # Fixed-point walk so the choice is reproducible from an integer alone.
        point = (rng_value % 1_000_000) / 1_000_000 * total
        acc = 0.0
        for p in profiles:
            acc += p.selection_weight
            if point < acc:
                return p
        return profiles[-1]

    def build_task(
        self,
        *,
        task_id: str,
        seed: str,
        toolchain_digest: str,
        profile: WorkloadProfile,
        mounted_repo: str = "mounted:///workspace/repo",
        network: str = "none",
    ) -> Task:
        """Materialise the concrete instance handed to the agent."""
        p = self.package
        bench_command = p.benchmark_command
        if profile.args:
            bench_command = f"{bench_command} {' '.join(profile.args)}"

        return Task(
            task_id=task_id,
            repository=RepositoryContract(
                uri=mounted_repo, revision=p.revision, license=p.license
            ),
            build=BuildContract(
                command=p.build_command,
                toolchain_digest=toolchain_digest,
                forbidden_flags=p.forbidden_flags,
                c_compiler=p.c_compiler,
                cxx_compiler=p.cxx_compiler,
            ),
            tests=TestContract(
                public_command=p.test_command,
                inventory_hash=inventory_hash(self.repo_dir, p.test_inventory_globs),
                differential_command=p.differential_command,
            ),
            equivalence=EquivalenceContract(
                discipline=p.equivalence_discipline,
                float_tolerance_ulp=p.float_tolerance_ulp,
                relative_error_budget=p.relative_error_budget,
                side_effects=p.side_effects,
            ),
            benchmark=BenchmarkContract(
                command=bench_command,
                objective=p.objective,
                measured_region=p.measured_region,
            ),
            patch_scope=PatchScope(
                patchable=p.patchable_paths,
                immutable_hash=immutable_tree_hash(self.repo_dir, p.patchable_paths),
            ),
            resources=p.resources,
            seed=seed,
            network=network,  # type: ignore[arg-type]
        )


@dataclass
class Corpus:
    """A snapshot of the whole task corpus.

    ``snapshot_id`` is part of the comparability tuple: two validators evaluating
    different corpus snapshots are not measuring the same thing, and must not
    compare scores.
    """

    snapshot_id: str
    packages: dict[str, LoadedPackage] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path, snapshot_id: str) -> Corpus:
        packages: dict[str, LoadedPackage] = {}
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not (child / "package.yaml").exists():
                continue
            loaded = LoadedPackage.load(child)
            packages[loaded.package.package_id] = loaded
        return cls(snapshot_id=snapshot_id, packages=packages)

    def public(self) -> list[LoadedPackage]:
        """Packages a miner is allowed to know exist."""
        return [p for p in self.packages.values() if not p.package.hidden_family]

    def hidden(self) -> list[LoadedPackage]:
        """The generalisation tier (Stage 6)."""
        return [p for p in self.packages.values() if p.package.hidden_family]

    def manifest(self) -> dict[str, Any]:
        """The public manifest. Hidden packages are listed by count only."""
        return {
            "snapshot_id": self.snapshot_id,
            "spec_digest": SPEC.digest(),
            "public_packages": [
                {
                    "package_id": p.package.package_id,
                    "family": str(p.package.family),
                    "revision": p.package.revision,
                    "license": p.package.license,
                    "content_hash": p.content_hash,
                    "profiles": [
                        {"name": w.name, "s_ref": w.s_ref_deterministic}
                        for w in p.package.workload_profiles
                        if w.published
                    ],
                    "s_ref": p.package.reference.s_ref_deterministic,
                }
                for p in sorted(self.public(), key=lambda x: x.package.package_id)
            ],
            "hidden_package_count": len(self.hidden()),
        }

    def manifest_hash(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
