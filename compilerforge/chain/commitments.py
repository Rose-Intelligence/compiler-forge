"""Reading and writing artifact commitments.

A miner enters the competition by writing one small payload to chain: the image
repository, its sha256 digest and the interface version. Everything else about the
artifact lives in the registry the digest points at.

The digest is doing real work. Because it is content-addressed and the tasks are
derived from a block hash that did not exist when the commitment was made, the
claim "this artifact was frozen before the task was chosen" becomes something a
third party can verify from public data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from compilerforge.protocol.commitment import ArtifactCommitment


@dataclass(frozen=True, slots=True)
class FrozenArtifact:
    """One miner's artifact, as it stood at the freeze block."""

    uid: int
    hotkey: str
    commitment: ArtifactCommitment
    frozen_at_block: int

    @property
    def digest(self) -> str:
        return self.commitment.digest

    @property
    def pull_reference(self) -> str:
        """The only form a validator is ever allowed to pull."""
        return self.commitment.pull_reference()


def earliest_commitment_times(artifacts: list[FrozenArtifact]) -> dict[str, datetime]:
    """digest -> earliest commitment time.

    Used as the dethronement tie-break: when two artifacts are indistinguishable
    on score, the one committed first wins, which removes any payoff from watching
    the leaderboard and cloning whoever is ahead.
    """
    out: dict[str, datetime] = {}
    for artifact in artifacts:
        when = artifact.commitment.committed_at
        if artifact.digest not in out or when < out[artifact.digest]:
            out[artifact.digest] = when
    return out


def resolve_hotkeys(artifacts: list[FrozenArtifact]) -> dict[str, str]:
    """digest -> hotkey, for turning per-artifact scores into a weight vector."""
    return {artifact.digest: artifact.hotkey for artifact in artifacts}
