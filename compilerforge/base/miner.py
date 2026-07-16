"""Base miner neuron.

A CompilerForge miner does not serve requests. It publishes an immutable artifact
and lets validators come and get it, which means the neuron's whole chain-facing
job is a single commitment:

    image repository + sha256 digest + interface version

The digest is what makes "this artifact was frozen before the task was chosen" a
checkable claim rather than a promise, so the neuron refuses to commit anything
it has not resolved to a concrete digest.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from compilerforge.base.neuron import BaseNeuron
from compilerforge.chain.access import ChainError
from compilerforge.protocol.commitment import ArtifactCommitment
from compilerforge.utils.config import NeuronConfig, add_miner_args
from compilerforge.utils.logging import logger
from compilerforge.utils.misc import short_digest


class ArtifactResolutionError(RuntimeError):
    """The image could not be resolved to a digest, so nothing may be committed."""


class BaseMinerNeuron(BaseNeuron):
    """Artifact commitment management for a miner."""

    neuron_type: str = "MinerNeuron"

    @classmethod
    def add_args(cls, parser) -> None:
        super().add_args(parser)
        add_miner_args(cls, parser)

    def __init__(self, config: NeuronConfig | None = None) -> None:
        super().__init__(config=config)
        self.committed: ArtifactCommitment | None = None
        self.load_state()

    # -- commitment ------------------------------------------------------

    def resolve_digest(self, image: str, container_cli: str = "docker") -> str:
        """Ask the registry for the image's content digest.

        Pulling by tag is never acceptable — a tag can be moved after the fact,
        which would defeat the entire freeze-before-entropy argument. Every
        failure path here raises: committing a digest this function guessed
        would be worse than not competing.
        """
        inspect = subprocess.run(  # noqa: S603 - operator-supplied image reference
            [container_cli, "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspect.returncode != 0:
            raise ArtifactResolutionError(
                f"cannot inspect {image}: {inspect.stderr.strip()[:200]}\n"
                "Build the image first, or pass --artifact.digest explicitly."
            )

        try:
            repo_digests = json.loads(inspect.stdout.strip() or "[]")
        except json.JSONDecodeError as exc:
            raise ArtifactResolutionError(
                f"unparseable registry metadata for {image}: {exc}"
            ) from exc

        for entry in repo_digests:
            if entry.startswith(image + "@"):
                return entry.split("@", 1)[1]
        if repo_digests:
            return str(repo_digests[0]).split("@", 1)[1]

        raise ArtifactResolutionError(
            f"{image} has no registry digest yet. Push it before committing — a "
            "digest no registry can serve is a commitment to nothing."
        )

    def build_commitment(self) -> ArtifactCommitment:
        image = self.config.artifact.image
        if not image:
            raise ArtifactResolutionError("--artifact.image is required")

        digest = self.config.artifact.digest or self.resolve_digest(
            image, self.config.get("sandbox", NeuronConfig()).get("container_cli", "docker")
        )
        cells = tuple(
            c.strip() for c in (self.config.artifact.cells or "generalist").split(",") if c.strip()
        )
        return ArtifactCommitment(
            image=image,
            digest=digest,
            agent_version=self.config.artifact.version,
            cells=cells,
        )

    def commit(self, commitment: ArtifactCommitment | None = None) -> bool:
        """Publish the artifact digest on chain."""
        commitment = commitment or self.build_commitment()

        if self.committed and self.committed.digest == commitment.digest:
            logger.info(
                f"Artifact {short_digest(commitment.digest)} is already committed"
            )
            return True

        logger.info(f"Committing {commitment.pull_reference()}")
        try:
            self.chain.set_commitment(self.wallet, commitment.encode())
        except ChainError as exc:
            logger.error(f"Commitment failed: {exc}")
            return False

        self.committed = commitment
        self.save_state()
        logger.success(f"Committed artifact {short_digest(commitment.digest)}")
        return True

    def current_commitment(self) -> ArtifactCommitment | None:
        """Read back what this hotkey has on chain.

        Returns None when nothing is committed. A commitment that exists but
        cannot be parsed is logged as a warning rather than treated as absent —
        it usually means another tool wrote to this hotkey's commitment slot,
        and silently overwriting it would hide that.
        """
        try:
            raw = self.chain.commitment_of(self.hotkey)
        except ChainError as exc:
            logger.error(f"Cannot read the on-chain commitment: {exc}")
            return None

        if not raw:
            return None
        try:
            return ArtifactCommitment.decode(raw)
        except Exception as exc:  # noqa: BLE001 - the payload is not ours to fix
            logger.warning(
                f"This hotkey has an on-chain commitment that is not a CompilerForge "
                f"artifact ({exc}). Committing will overwrite it."
            )
            return None

    # -- persistence -----------------------------------------------------

    @property
    def state_path(self) -> Path:
        return Path(self.config.neuron.full_path) / "miner_state.json"

    def save_state(self) -> None:
        if self.committed is None:
            return
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(self.committed.encode())
        tmp.replace(self.state_path)

    def load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            self.committed = ArtifactCommitment.decode(self.state_path.read_text())
        except Exception as exc:  # noqa: BLE001
            # Losing the cached commitment only costs one redundant chain call,
            # so this is recoverable — but a corrupt state file is worth saying.
            logger.warning(f"Ignoring unreadable miner state {self.state_path}: {exc}")
            self.committed = None
