"""The on-chain miner commitment.

A miner commits an image digest *before* any task is known. Because the digest is
content-addressed and the task is derived from a block hash that did not exist
when the commitment was made, "the artifact was frozen before the task was chosen"
becomes a claim a third party can check from public data rather than a promise.

Commitments are kept small: chain commitment space is limited, and everything
large belongs in the registry the digest points at.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from compilerforge.spec import INTERFACE_VERSION, SPEC_VERSION

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Chain commitment payloads are bounded; keep well inside the limit.
MAX_COMMITMENT_BYTES = 512


class ArtifactCommitment(BaseModel):
    """What a miner writes to chain to enter the competition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    v: int = SPEC_VERSION
    iface: str = INTERFACE_VERSION
    #: Registry reference, e.g. ghcr.io/miner/agent. Never used for pulling on its
    #: own — the digest is what gets pulled ("never pull by tag").
    image: str
    digest: str
    #: Free-form, informational only. Never scored.
    agent_version: str = "0.0.0"
    #: Which mechanism/cells this artifact is entering.
    cells: tuple[str, ...] = ("generalist",)
    committed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("digest")
    @classmethod
    def _digest_form(cls, v: str) -> str:
        if not _DIGEST_RE.match(v):
            raise ValueError(f"digest must be sha256:<64 hex chars>, got {v!r}")
        return v

    @field_validator("image")
    @classmethod
    def _no_tag_reliance(cls, v: str) -> str:
        if "@" in v:
            raise ValueError("image must be a bare repository reference; the digest field pins it")
        return v

    def encode(self) -> str:
        """Compact JSON for the chain commitment field."""
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        if len(payload.encode()) > MAX_COMMITMENT_BYTES:
            raise ValueError(
                f"commitment payload is {len(payload.encode())} bytes, limit is {MAX_COMMITMENT_BYTES}"
            )
        return payload

    @classmethod
    def decode(cls, raw: str) -> ArtifactCommitment:
        return cls.model_validate_json(raw)

    def pull_reference(self) -> str:
        """The only form a validator is ever allowed to pull."""
        return f"{self.image}@{self.digest}"
