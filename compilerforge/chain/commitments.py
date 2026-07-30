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
from datetime import UTC, datetime

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
    """digest -> a trustworthy commitment ordering, the dethronement tie-break.

    When two artifacts are indistinguishable on score, the one committed first
    wins, which removes any payoff from watching the leaderboard and cloning
    whoever is ahead. The ordering is taken from the chain block the artifact was
    frozen at — the chain's own record — NOT the ``committed_at`` inside the
    miner's payload, which a cloner would set to the minimum to win every tie. The
    block is rendered as a datetime purely so the ordering interface is unchanged;
    only the relative order is ever used.
    """
    out: dict[str, datetime] = {}
    for artifact in artifacts:
        when = datetime.fromtimestamp(artifact.frozen_at_block, tz=UTC)
        if artifact.digest not in out or when < out[artifact.digest]:
            out[artifact.digest] = when
    return out


def resolve_hotkeys(artifacts: list[FrozenArtifact]) -> dict[str, str]:
    """digest -> hotkey, for turning per-artifact scores into a weight vector."""
    return {artifact.digest: artifact.hotkey for artifact in artifacts}
