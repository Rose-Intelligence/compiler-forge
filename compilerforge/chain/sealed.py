"""Sealed hidden task material via drand timelock.

Timelock encryption binds data to a future round of the drand quicknet beacon.
Rounds publish every few seconds worldwide, and before the target round nobody
can decrypt — *including the author*.

Why it is worth doing, in one sentence: without it, "the validator did not peek
at the hidden tests" is a statement of trust; with it, the ciphertext was public
before the round, the reveal round is embedded in the ciphertext itself, and a
third party can verify the timeline from public data alone. That is the
difference between a benchmark customers believe and a benchmark customers audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from bittensor import timelock

from compilerforge.evaluation.differential import DifferentialCase


class TimelockNotReady(RuntimeError):
    """The reveal round has not arrived. Not an error — the mechanism working."""


class SealError(RuntimeError):
    """Sealing or opening failed for a reason other than simply being too early."""


@dataclass(frozen=True, slots=True)
class SealedEnvelope:
    """Published before the round; openable only after it.

    Anyone can archive this; nobody can open it early. The ``reveal_round`` is
    what a third party checks against the drand beacon to confirm the timeline.
    """

    label: str
    ciphertext_hex: str
    reveal_round: int
    #: Hash of the plaintext, so a revealed payload can be checked against what
    #: was actually committed to rather than against whatever gets published later.
    plaintext_digest: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "label": self.label,
                "ciphertext_hex": self.ciphertext_hex,
                "reveal_round": self.reveal_round,
                "plaintext_digest": self.plaintext_digest,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> SealedEnvelope:
        data = json.loads(raw)
        missing = {"label", "ciphertext_hex", "reveal_round", "plaintext_digest"} - set(data)
        if missing:
            raise SealError(f"sealed envelope is missing {', '.join(sorted(missing))}")
        return cls(
            label=data["label"],
            ciphertext_hex=data["ciphertext_hex"],
            reveal_round=int(data["reveal_round"]),
            plaintext_digest=data["plaintext_digest"],
        )

    def write(self, path: Path) -> None:
        path.write_text(self.to_json())

    @classmethod
    def read(cls, path: Path) -> SealedEnvelope:
        return cls.from_json(path.read_text())


def seal(data: bytes, *, label: str, reveal_in: str) -> SealedEnvelope:
    """Encrypt ``data`` so it cannot be read until ``reveal_in`` has elapsed.

    ``reveal_in`` is a duration string the SDK parses, such as ``"36h"``.
    """
    try:
        sealed = timelock.encrypt(data, reveal_in)
    except Exception as exc:  # noqa: BLE001 - surfaced with context, never ignored
        raise SealError(f"could not seal {label!r}: {exc}") from exc

    return SealedEnvelope(
        label=label,
        ciphertext_hex=sealed.ciphertext.hex(),
        reveal_round=int(sealed.reveal_round),
        plaintext_digest="sha256:" + hashlib.sha256(data).hexdigest(),
    )


def seal_for_hours(data: bytes, *, label: str, hours: float) -> SealedEnvelope:
    """Seal for a wall-clock duration expressed in hours."""
    return seal(data, label=label, reveal_in=f"{hours}h")


def open_envelope(
    envelope: SealedEnvelope, *, verify_digest: bool = True, wait: bool = False
) -> bytes:
    """Decrypt after the reveal round.

    Raises :class:`TimelockNotReady` before the round arrives, which is the
    expected path during a live round — validators call this only after the
    artifact set is frozen and the round has closed.

    ``verify_digest`` is on by default and should stay on: it is what makes the
    revealed material provably the same material that was committed to, rather
    than whatever the publisher chose to release afterwards.
    """
    try:
        plaintext = timelock.decrypt(bytes.fromhex(envelope.ciphertext_hex), wait=wait)
    except timelock.TimelockNotReady as exc:
        raise TimelockNotReady(
            f"drand round {envelope.reveal_round} has not been published yet; the "
            "sealed material is not readable by anyone, including this validator"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise SealError(f"could not open {envelope.label!r}: {exc}") from exc

    if not plaintext:
        raise TimelockNotReady(
            f"drand round {envelope.reveal_round} has not been published yet"
        )

    if verify_digest:
        actual = "sha256:" + hashlib.sha256(plaintext).hexdigest()
        if actual != envelope.plaintext_digest:
            raise SealError(
                f"revealed material does not match the sealed digest for "
                f"{envelope.label!r} (expected {envelope.plaintext_digest}, got {actual})"
            )
    return plaintext


def seal_differential_cases(
    cases: list[DifferentialCase], *, label: str, hours: float
) -> SealedEnvelope:
    """Seal a hidden differential corpus for one round."""
    if not cases:
        raise SealError(
            f"refusing to seal an empty differential corpus for {label!r}; a task "
            "with no hidden inputs cannot be differentially tested"
        )
    payload = {
        "cases": [
            {
                "case_id": c.case_id,
                "argv": list(c.argv),
                "stdin_hex": c.stdin.hex(),
                "files_hex": {k: v.hex() for k, v in (c.files or {}).items()},
            }
            for c in cases
        ]
    }
    return seal_for_hours(json.dumps(payload).encode(), label=label, hours=hours)


def wait_and_open(envelope: SealedEnvelope) -> bytes:
    """Block until the reveal round, then decrypt.

    Only appropriate in the post-round audit path. A validator's scoring loop
    should never block on the beacon.
    """
    return open_envelope(envelope, wait=True)
