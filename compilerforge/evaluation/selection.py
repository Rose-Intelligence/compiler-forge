"""Stage 0 — task commitment and selection.

The order is the whole security argument:

  1. The miner artifact set is frozen (on-chain commitments, content-addressed).
  2. A *future* block hash is drawn.
  3. That hash selects repository, revision, workload profile, input seed and
     benchmark ordering from the versioned corpus.

Because step 2 produces entropy that did not exist at step 1, "the artifact was
tuned to this task" is not merely discouraged — it is checkable from public data.

Everything here is a pure function of (block hash, corpus snapshot, spec digest).
Two validators drawing the same block hash must derive byte-identical tasks, so
no wall-clock, no local randomness and no dict ordering may leak in.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TypeVar

from compilerforge.corpus.package import Corpus, LoadedPackage, WorkloadProfile
from compilerforge.protocol.task import Task
from compilerforge.spec import SPEC

T = TypeVar("T")


class SelectionError(RuntimeError):
    """Raised when a round cannot be derived. Validators fail closed."""


@dataclass(frozen=True, slots=True)
class RoundSeed:
    """Entropy for one round, bound to the block that produced it."""

    block_number: int
    block_hash: str
    corpus_snapshot: str
    spec_digest: str

    def stream(self, label: str) -> int:
        """A labelled 256-bit draw.

        Separate labels give independent streams from the same block hash, so
        task selection cannot be nudged by changing how many draws precede it.
        """
        payload = f"{self.block_hash}|{self.corpus_snapshot}|{self.spec_digest}|{label}"
        return int.from_bytes(hashlib.sha256(payload.encode()).digest(), "big")


@dataclass(frozen=True, slots=True)
class SelectedTask:
    """One concrete evaluation instance, plus the provenance to re-derive it."""

    task: Task
    package: LoadedPackage
    profile: WorkloadProfile
    hidden: bool

    @property
    def package_id(self) -> str:
        return self.package.package.package_id


@dataclass(frozen=True, slots=True)
class RoundPlan:
    """Everything a round evaluates, derived deterministically from one block."""

    seed: RoundSeed
    tasks: tuple[SelectedTask, ...]
    toolchain_digest: str

    @property
    def public_tasks(self) -> tuple[SelectedTask, ...]:
        return tuple(t for t in self.tasks if not t.hidden)

    @property
    def hidden_tasks(self) -> tuple[SelectedTask, ...]:
        """The Stage 6 generalisation tier."""
        return tuple(t for t in self.tasks if t.hidden)

    def manifest(self) -> dict:
        """The publishable task manifest.

        Hidden packages are named only after the round closes, so this manifest
        commits to *how many* hidden tasks ran without revealing which.
        """
        return {
            "block_number": self.seed.block_number,
            "block_hash": self.seed.block_hash,
            "corpus_snapshot": self.seed.corpus_snapshot,
            "spec_digest": self.seed.spec_digest,
            "toolchain_digest": self.toolchain_digest,
            "public_tasks": [
                {
                    "task_id": t.task.task_id,
                    "package_id": t.package_id,
                    "profile": t.profile.name,
                    "seed": t.task.seed,
                }
                for t in self.public_tasks
            ],
            "hidden_task_count": len(self.hidden_tasks),
        }

    def manifest_hash(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def derive_round(
    *,
    seed: RoundSeed,
    corpus: Corpus,
    toolchain_digest: str,
    public_task_count: int = 25,
    hidden_task_count: int = 3,
) -> RoundPlan:
    """Derive the round's task set from a future block hash.

    Selection is without replacement across packages: one repository appearing
    twice in a round would let a single library family carry an artifact, which
    the aggregation rules explicitly guard against.
    """
    if seed.corpus_snapshot != corpus.snapshot_id:
        raise SelectionError(
            f"round seed pins corpus {seed.corpus_snapshot}, got {corpus.snapshot_id}"
        )
    if seed.spec_digest != SPEC.digest():
        raise SelectionError(
            "round seed was derived under a different consensus spec; refusing to evaluate"
        )

    public = sorted(corpus.public(), key=lambda p: p.package.package_id)
    hidden = sorted(corpus.hidden(), key=lambda p: p.package.package_id)

    if not public:
        raise SelectionError("corpus contains no public packages")
    if hidden_task_count > 0 and not hidden:
        # At least one hidden family is required per round. A corpus
        # that cannot supply one produces no round at all.
        raise SelectionError(
            "round requires a hidden generalisation family but the corpus has none"
        )

    chosen_public = _sample_without_replacement(
        public, min(public_task_count, len(public)), seed.stream("public-packages")
    )
    chosen_hidden = _sample_without_replacement(
        hidden, min(hidden_task_count, len(hidden)), seed.stream("hidden-packages")
    )

    tasks: list[SelectedTask] = []
    for is_hidden, group in ((False, chosen_public), (True, chosen_hidden)):
        for index, pkg in enumerate(group):
            label = f"{'hidden' if is_hidden else 'public'}-{pkg.package.package_id}-{index}"
            draw = seed.stream(label)
            profile = pkg.select_profile(draw >> 64)
            task_seed = f"0x{(draw & ((1 << 128) - 1)):032x}"
            task_id = _task_id(seed, pkg, profile, task_seed)
            task = pkg.build_task(
                task_id=task_id,
                seed=task_seed,
                toolchain_digest=toolchain_digest,
                profile=profile,
            )
            tasks.append(
                SelectedTask(task=task, package=pkg, profile=profile, hidden=is_hidden)
            )

    return RoundPlan(seed=seed, tasks=tuple(tasks), toolchain_digest=toolchain_digest)


def _task_id(
    seed: RoundSeed, pkg: LoadedPackage, profile: WorkloadProfile, task_seed: str
) -> str:
    payload = "|".join(
        [
            seed.block_hash,
            seed.corpus_snapshot,
            seed.spec_digest,
            pkg.package.package_id,
            pkg.content_hash,
            profile.name,
            task_seed,
        ]
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _sample_without_replacement(items: list[T], k: int, entropy: int) -> list[T]:
    """Deterministic Fisher-Yates prefix, driven by one integer.

    Written out rather than using ``random.Random(seed)`` because the standard
    library's Mersenne Twister stream is an implementation detail that could
    change between Python versions — and a validator upgrading Python must not
    silently start evaluating a different task set.
    """
    pool = list(items)
    n = len(pool)
    k = min(k, n)
    state = entropy
    for i in range(k):
        state = int.from_bytes(hashlib.sha256(state.to_bytes(32, "big")).digest(), "big")
        j = i + (state % (n - i))
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:k]
