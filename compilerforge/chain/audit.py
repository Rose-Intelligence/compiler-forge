"""The public audit repository.

SN17 404-GEN keeps all competition state in a public git repository so every stage
transition is traceable. CompilerForge adopts the same discipline, because it is
the credibility argument the commercial product is actually selling: anyone can
re-run a round from public data and reach the same ranking.

After each round the operator publishes:

  * the task manifest and its content hash
  * the timelock ciphertext and the revealed material
  * every validator's signed score artifact
  * the aggregated weight vector
  * the accepted patches for public-corpus tasks

A leaderboard that shows only miner scores is a marketing artifact. The bundle
also carries the reference ladder — unmodified, ``-O2``, ``-O3``, PGO, the naive
LLM patcher, and the SDK reference agent — which is what turns it into a research
instrument.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from compilerforge.chain.sealed import SealedEnvelope
from compilerforge.protocol.score import ScoreArtifact
from compilerforge.spec import SPEC
from compilerforge.utils.logging import logger


@dataclass(frozen=True, slots=True)
class ReferenceLadderEntry:
    """One rung of the published control ladder."""

    name: str
    deterministic_speedup: float
    description: str


@dataclass(slots=True)
class RoundBundle:
    """Everything needed to reproduce one round from public data."""

    round_number: int
    block_number: int
    block_hash: str
    corpus_snapshot: str
    toolchain_digest: str
    spec_digest: str = field(default_factory=SPEC.digest)

    task_manifest: dict = field(default_factory=dict)
    sealed_envelopes: list[SealedEnvelope] = field(default_factory=list)
    revealed_material: dict[str, str] = field(default_factory=dict)
    score_artifacts: list[ScoreArtifact] = field(default_factory=list)
    weight_vectors: dict[int, dict[str, float]] = field(default_factory=dict)
    accepted_patches: dict[str, str] = field(default_factory=dict)
    reference_ladder: list[ReferenceLadderEntry] = field(default_factory=list)
    voided_tasks: dict[str, str] = field(default_factory=dict)
    champion_digest: str | None = None
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "round_number": self.round_number,
            "block_number": self.block_number,
            "block_hash": self.block_hash,
            "corpus_snapshot": self.corpus_snapshot,
            "toolchain_digest": self.toolchain_digest,
            "spec_digest": self.spec_digest,
            "task_manifest": self.task_manifest,
            "sealed_envelopes": [
                {
                    "label": e.label,
                    "reveal_round": e.reveal_round,
                    "plaintext_digest": e.plaintext_digest,
                    "ciphertext_hex": e.ciphertext_hex,
                }
                for e in self.sealed_envelopes
            ],
            "revealed_material": self.revealed_material,
            "score_artifacts": [a.model_dump(mode="json") for a in self.score_artifacts],
            "weight_vectors": {str(k): v for k, v in self.weight_vectors.items()},
            "voided_tasks": self.voided_tasks,
            "champion_digest": self.champion_digest,
            "reference_ladder": [asdict(r) for r in self.reference_ladder],
            "published_at": self.published_at.isoformat(),
        }

    def bundle_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def write(self, root: Path) -> Path:
        """Lay the bundle out as files a git repository can carry."""
        round_dir = root / f"round-{self.round_number:06d}"
        round_dir.mkdir(parents=True, exist_ok=True)

        (round_dir / "round.json").write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))

        patches_dir = round_dir / "patches"
        if self.accepted_patches:
            patches_dir.mkdir(exist_ok=True)
            for task_id, patch in sorted(self.accepted_patches.items()):
                name = task_id.removeprefix("sha256:")[:32]
                (patches_dir / f"{name}.patch").write_text(patch)

        (round_dir / "BUNDLE_HASH").write_text(self.bundle_hash() + "\n")
        return round_dir

    def verification_instructions(self) -> str:
        """What a third party runs to reach the same ranking."""
        return f"""\
# Reproduce round {self.round_number} from public data

# 1. Confirm the task set was derived from a block that postdates every commitment
#    Block {self.block_number}, hash {self.block_hash}
btcli query block --block {self.block_number}

# 2. Confirm the hidden material could not have been read early. Each envelope's
#    reveal_round is a drand quicknet round number; check it was in the future
#    when the ciphertext was first published.

# 3. Re-derive the task set. Same block hash + same corpus snapshot + same spec
#    digest must produce byte-identical tasks.
cf-corpus derive-round \\
    --block-hash {self.block_hash} \\
    --corpus-snapshot {self.corpus_snapshot} \\
    --spec-digest {self.spec_digest}

# 4. Re-run the measurements. Tier A is hardware-independent by construction, so
#    any x86-64 host with the pinned toolchain must reproduce the instruction
#    counts in score_artifacts within {SPEC.measurement.tier_a_cross_validator_tolerance:.3%}.
cf-eval verify-bundle round-{self.round_number:06d}/round.json

# 5. Compare against the published reference ladder. A champion that does not beat
#    -O3 and the naive LLM control on hidden families has not demonstrated anything.
"""


@dataclass(slots=True)
class AuditRepository:
    """Append-only local view of the public audit repository."""

    root: Path

    def publish(self, bundle: RoundBundle) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        round_dir = bundle.write(self.root)
        (round_dir / "VERIFY.md").write_text(bundle.verification_instructions())
        self._update_index(bundle)
        return round_dir

    def _update_index(self, bundle: RoundBundle) -> None:
        index_path = self.root / "index.json"
        index: dict = {"rounds": []}

        if index_path.exists():
            try:
                loaded = json.loads(index_path.read_text())
                if isinstance(loaded, dict) and isinstance(loaded.get("rounds"), list):
                    index = loaded
                else:
                    raise ValueError("index.json is not a rounds object")
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                # The per-round directories are the real record; the index is a
                # convenience. Rebuilding it from them is safe, but losing it
                # silently would make published rounds look like they never
                # happened, so the recovery is announced and the damaged file kept.
                damaged = index_path.with_suffix(".json.corrupt")
                logger.error(
                    "audit index %s is unreadable (%s); preserving it as %s and "
                    "rebuilding from the round directories",
                    index_path,
                    exc,
                    damaged,
                )
                index_path.replace(damaged)
                index = {"rounds": self._rebuild_index_entries()}

        index["rounds"] = [
            r for r in index["rounds"] if r.get("round_number") != bundle.round_number
        ]
        index["rounds"].append(
            {
                "round_number": bundle.round_number,
                "block_number": bundle.block_number,
                "bundle_hash": bundle.bundle_hash(),
                "champion_digest": bundle.champion_digest,
                "spec_digest": bundle.spec_digest,
                "published_at": bundle.published_at.isoformat(),
            }
        )
        index["rounds"].sort(key=lambda r: r.get("round_number", 0))

        # Written atomically: a crash mid-write would otherwise leave a truncated
        # index behind, and the next round would report it as corrupt.
        tmp = index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2))
        tmp.replace(index_path)

    def _rebuild_index_entries(self) -> list[dict]:
        """Recover index entries by reading the published round directories."""
        entries: list[dict] = []
        for round_dir in sorted(self.root.glob("round-*")):
            payload = round_dir / "round.json"
            if not payload.exists():
                continue
            try:
                data = json.loads(payload.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skipping unreadable round bundle %s: %s", payload, exc)
                continue
            entries.append(
                {
                    "round_number": data.get("round_number"),
                    "block_number": data.get("block_number"),
                    "bundle_hash": (round_dir / "BUNDLE_HASH").read_text().strip()
                    if (round_dir / "BUNDLE_HASH").exists()
                    else None,
                    "champion_digest": data.get("champion_digest"),
                    "spec_digest": data.get("spec_digest"),
                    "published_at": data.get("published_at"),
                }
            )
        return entries
