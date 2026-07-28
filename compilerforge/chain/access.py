"""Every chain read and write this subnet performs.

Concentrated in one module so that an SDK change is a change here rather than a
change in nine places, and so that every call site has a single, auditable place
where "the chain said no" is turned into an exception rather than a shrug.

Written against the Bittensor 11 model: reads go through ``subtensor.read(name,
**params)``, writes are intents executed with a wallet, and anything without a
high-level intent uses the generated call directly.

Nothing here returns a plausible default on failure. A validator that cannot read
the chain must produce no weights, never guessed ones, so every method either
returns real data or raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bittensor as bt

from compilerforge.utils.logging import logger

#: Commitment payloads are stored as a list of Raw<N> field variants. The pallet
#: caps a single Raw field at 512 bytes, which is comfortably above the
#: commitment size this subnet uses.
MAX_RAW_FIELD_BYTES = 512


class ChainError(RuntimeError):
    """A chain operation that did not succeed. Never swallowed by a caller."""


@dataclass(frozen=True, slots=True)
class NeuronRecord:
    """One neuron as the metagraph reports it."""

    uid: int
    hotkey: str
    coldkey: str
    stake: float
    validator_permit: bool
    last_update: int
    incentive: float
    emission: float


@dataclass(frozen=True, slots=True)
class MetagraphSnapshot:
    """A metagraph read, plus the commitments that came with it.

    Bittensor 11 returns commitments as part of the metagraph, so freezing the
    artifact set and reading the neuron table are one round trip rather than two
    that could observe different chain states.
    """

    netuid: int
    block: int
    neurons: tuple[NeuronRecord, ...]
    #: hotkey -> committed payload string
    commitments: dict[str, str]

    def uid_of(self, hotkey: str) -> int | None:
        for neuron in self.neurons:
            if neuron.hotkey == hotkey:
                return neuron.uid
        return None

    def hotkeys(self) -> dict[str, int]:
        return {n.hotkey: n.uid for n in self.neurons}

    def is_registered(self, hotkey: str) -> bool:
        return self.uid_of(hotkey) is not None


@dataclass(slots=True)
class ChainAccess:
    """A connected view of one subnet."""

    netuid: int
    network: str = "finney"
    fallback_endpoints: tuple[str, ...] = ()
    _subtensor: Any = None

    @property
    def subtensor(self) -> Any:
        if self._subtensor is None:
            try:
                self._subtensor = bt.Subtensor(
                    self.network,
                    fallback_endpoints=list(self.fallback_endpoints) or None,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced with context, never ignored
                raise ChainError(
                    f"cannot connect to '{self.network}': {exc}"
                ) from exc
        return self._subtensor

    def close(self) -> None:
        if self._subtensor is not None:
            try:
                self._subtensor.close()
            finally:
                self._subtensor = None

    # -- reads -----------------------------------------------------------

    def _read(self, name: str, **params: Any) -> Any:
        try:
            return self.subtensor.read(name, **params)
        except Exception as exc:  # noqa: BLE001
            raise ChainError(f"chain read '{name}' failed: {exc}") from exc

    def current_block(self) -> int:
        try:
            return int(self.subtensor.block)
        except Exception as exc:  # noqa: BLE001
            raise ChainError(f"cannot read the current block: {exc}") from exc

    def block_hash(self, block: int) -> str:
        """The entropy that selects a round's tasks.

        Refuses a block that has not been produced. Asking for a future block is
        the specific mistake that would break the freeze-before-entropy ordering,
        so it fails loudly rather than returning an empty string.
        """
        head = self.current_block()
        if block > head:
            raise ChainError(
                f"block {block} has not been produced yet (head is {head}); the "
                "task-selecting hash must come from a block that already exists"
            )

        info = self._read("block_info", block=block)
        block_hash = _attr(info, "hash")
        if not block_hash:
            raise ChainError(f"chain returned no hash for block {block}")
        return str(block_hash)

    def metagraph(self) -> MetagraphSnapshot:
        """Read the neuron table and the commitments that came with it."""
        raw = self._read("metagraph", netuid=self.netuid)
        if raw is None:
            raise ChainError(f"netuid {self.netuid} returned no metagraph")

        neurons: list[NeuronRecord] = []
        entries = _attr(raw, "neurons")
        if entries:
            # A per-neuron object/row shape.
            for entry in entries:
                neurons.append(
                    NeuronRecord(
                        uid=int(_attr(entry, "uid") or 0),
                        hotkey=str(_attr(entry, "hotkey") or ""),
                        coldkey=str(_attr(entry, "coldkey") or ""),
                        stake=_as_float(_attr(entry, "total_stake")),
                        validator_permit=bool(_attr(entry, "validator_permit")),
                        last_update=int(_attr(entry, "last_update") or 0),
                        incentive=_as_float(_attr(entry, "incentive")),
                        emission=_as_float(_attr(entry, "emission")),
                    )
                )
        else:
            # Bittensor 11's ``metagraph`` read returns parallel column arrays
            # (hotkeys[], validator_permit[], total_stake[], ...) rather than a
            # ``neurons`` list. The uid is the array index. ``incentives`` is the
            # plural key here.
            hotkeys = _attr(raw, "hotkeys") or []
            coldkeys = _attr(raw, "coldkeys") or []
            permits = _attr(raw, "validator_permit") or []
            stakes = _attr(raw, "total_stake") or []
            updates = _attr(raw, "last_update") or []
            incentives = _attr(raw, "incentives") or _attr(raw, "incentive") or []
            emissions = _attr(raw, "emission") or []

            def _at(seq: Any, i: int, default: Any = None) -> Any:
                return seq[i] if i < len(seq) else default

            for uid in range(len(hotkeys)):
                neurons.append(
                    NeuronRecord(
                        uid=uid,
                        hotkey=str(_at(hotkeys, uid) or ""),
                        coldkey=str(_at(coldkeys, uid) or ""),
                        stake=_as_float(_at(stakes, uid)),
                        validator_permit=bool(_at(permits, uid)),
                        last_update=int(_at(updates, uid) or 0),
                        incentive=_as_float(_at(incentives, uid)),
                        emission=_as_float(_at(emissions, uid)),
                    )
                )

        # Bittensor 11's ``metagraph`` read does not carry commitments, so fetch
        # them with a second read and attach — callers expect the artifact set
        # and the neuron table to come back together.
        commitments: dict[str, str] = {}
        carried = _attr(raw, "commitments")
        if carried:
            for record in carried.values():
                hotkey = _attr(record, "hotkey")
                data = _attr(record, "commitment") or _attr(record, "data")
                if hotkey and data:
                    commitments[str(hotkey)] = str(data)
        else:
            commitments = self.commitments()

        return MetagraphSnapshot(
            netuid=self.netuid,
            block=int(_attr(raw, "block") or self.current_block()),
            neurons=tuple(neurons),
            commitments=commitments,
        )

    def commitments(self) -> dict[str, str]:
        """hotkey -> payload, for every commitment on the subnet."""
        records = self._read("commitments", netuid=self.netuid)
        out: dict[str, str] = {}
        for record in records or []:
            hotkey = _attr(record, "hotkey")
            # Bittensor 11 names the payload ``commitment``; older reads used
            # ``data``. Accept either.
            data = _attr(record, "commitment") or _attr(record, "data")
            if hotkey and data:
                out[str(hotkey)] = str(data)
        return out

    def commitment_of(self, hotkey: str) -> str | None:
        record = self._read("commitment", netuid=self.netuid, hotkey_ss58=hotkey)
        if record is None:
            return None
        data = _attr(record, "commitment") or _attr(record, "data")
        return str(data) if data else None

    def hyperparameters(self) -> dict[str, Any]:
        params = self._read("subnet_hyperparameters", netuid=self.netuid)
        if params is None:
            raise ChainError(f"netuid {self.netuid} returned no hyperparameters")
        return dict(params) if not isinstance(params, dict) else params

    def commit_reveal_enabled(self) -> bool:
        return bool(self._read("commit_reveal_enabled", netuid=self.netuid))

    # -- writes ----------------------------------------------------------

    def _execute(self, intent: Any, wallet: Any, *, what: str) -> Any:
        """Run an intent and insist the chain confirmed it.

        The SDK reports failure in the returned result rather than by raising,
        so a caller that ignores the return value would treat a rejected
        extrinsic as a success. This converts that into an exception once, here.
        """
        try:
            result = self.subtensor.execute(intent, wallet)
        except Exception as exc:  # noqa: BLE001
            raise ChainError(f"{what} failed to submit: {exc}") from exc

        if not _succeeded(result):
            raise ChainError(f"{what} was rejected: {_failure_message(result)}")
        return result

    def set_weights(
        self,
        wallet: Any,
        *,
        uids: list[int],
        weights: list[float],
        mechid: int = 0,
        version_key: int = 0,
    ) -> Any:
        """Submit a weight vector for one mechanism.

        Commit-reveal is a subnet hyperparameter, and the SDK picks plaintext or
        commit-reveal automatically from it — nothing here should reimplement
        that choice.
        """
        if not uids:
            raise ChainError("refusing to set an empty weight vector")
        if len(uids) != len(weights):
            raise ChainError(
                f"weight vector length mismatch: {len(uids)} uids, {len(weights)} weights"
            )

        return self._execute(
            bt.SetWeights(
                netuid=self.netuid,
                uids=uids,
                weights=weights,
                mechid=mechid,
                version_key=version_key,
            ),
            wallet,
            what=f"set_weights on mechanism {mechid}",
        )

    def set_commitment(self, wallet: Any, payload: str) -> Any:
        """Publish a commitment payload for the signing hotkey.

        Bittensor 11 exposes no high-level intent for this, so the generated
        ``Commitments.set_commitment`` call is composed directly. The payload is
        stored as Raw field variants, which is the shape the commitment reads
        decode back into a string.
        """
        data = payload.encode()
        if len(data) > MAX_RAW_FIELD_BYTES:
            raise ChainError(
                f"commitment payload is {len(data)} bytes, above the "
                f"{MAX_RAW_FIELD_BYTES}-byte limit"
            )

        # The commitment ``Data`` enum only defines Raw0..Raw128, so a payload
        # larger than 128 bytes must be split across several Raw fields. The read
        # side receives them already concatenated back into one ``data`` string.
        chunk = 128
        fields = [
            {f"Raw{len(data[i : i + chunk])}": "0x" + data[i : i + chunk].hex()}
            for i in range(0, len(data), chunk)
        ] or [{"Raw0": "0x"}]

        call = bt.calls.Commitments.set_commitment(
            netuid=self.netuid,
            info={"fields": [fields]},
        )
        try:
            # A commitment is made by the registered hotkey, not the coldkey; the
            # pallet rejects a coldkey signer with "not allowed to make commitments".
            result = self.subtensor.submit_call(call, wallet, signer="hotkey")
        except Exception as exc:  # noqa: BLE001
            raise ChainError(f"set_commitment failed to submit: {exc}") from exc

        if not _succeeded(result):
            raise ChainError(f"set_commitment was rejected: {_failure_message(result)}")
        return result

    def wait_for_block(self, block: int, *, timeout: float | None = None) -> None:
        try:
            self.subtensor.wait_for_block(block, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise ChainError(f"waiting for block {block} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dataclass or a mapping.

    The SDK returns dataclasses for some reads and plain dicts for others, and a
    caller that assumed one shape would silently read ``None`` from the other.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_float(value: Any) -> float:
    """Coerce a Balance, Decimal or number to float, or 0.0 if it is absent."""
    if value is None:
        return 0.0
    for attr in ("tao", "value"):
        inner = getattr(value, attr, None)
        if inner is not None:
            value = inner
            break
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.debug("could not coerce %r to float; treating as 0.0", value)
        return 0.0


def _succeeded(result: Any) -> bool:
    """Whether an extrinsic result reports success.

    Defaults to *False* for an unrecognised shape. Guessing "probably fine" here
    would report weights as set when they were not.
    """
    if result is None:
        return False
    success = _attr(result, "success")
    if success is None:
        return False
    return bool(success)


def _failure_message(result: Any) -> str:
    for field in ("message", "error"):
        value = _attr(result, field)
        if value:
            return str(value)
    return repr(result)[:300]
