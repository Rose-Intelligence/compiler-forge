"""Domain-specific equivalence comparators.

Writing one good comparator is easy. Writing thirty, across seven workload
families, such that none can be gamed and none rejects a legitimate optimization,
is the bulk of the corpus work, and one of the few places in this system where
the engineering risk is genuinely novel rather than integration work.

Two rules hold for every comparator here:

1. The task package chooses the discipline. The agent never does. Choosing a laxer
   comparator would be the cheapest attack in the system.
2. A comparator reports *evidence*, never proof. Passing a finite differential
   suite is not universal equivalence, and every report must say so.
"""

from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Protocol

from compilerforge.protocol.task import EquivalenceContract, EquivalenceDiscipline


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything observable about one execution, as declared by the contract."""

    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int = 0
    #: relpath -> content, for the files the contract declares as side effects.
    written_files: dict[str, bytes] = field(default_factory=dict)
    #: Structured state for stateful/service families.
    state: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Divergence:
    channel: str
    detail: str


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    equivalent: bool
    divergences: tuple[Divergence, ...] = ()

    @property
    def summary(self) -> str:
        if self.equivalent:
            return "no divergence"
        return "; ".join(f"{d.channel}: {d.detail}" for d in self.divergences[:5])


class Comparator(Protocol):
    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult: ...


def _compare_declared_channels(
    baseline: Observation,
    candidate: Observation,
    contract: EquivalenceContract,
    *,
    stdout_equal: bool | None = None,
) -> list[Divergence]:
    """Compare the non-payload channels every discipline shares.

    ``stdout_equal`` lets a discipline supply its own verdict for stdout while
    still getting exit code, stderr shape and written-file comparison for free.
    """
    out: list[Divergence] = []
    declared = set(contract.side_effects)

    if "exit_code" in declared and baseline.exit_code != candidate.exit_code:
        out.append(
            Divergence("exit_code", f"baseline={baseline.exit_code} candidate={candidate.exit_code}")
        )

    if "stdout" in declared:
        equal = (baseline.stdout == candidate.stdout) if stdout_equal is None else stdout_equal
        if not equal:
            out.append(Divergence("stdout", _describe_bytes_diff(baseline.stdout, candidate.stdout)))

    if "stderr" in declared and baseline.stderr != candidate.stderr:
        out.append(Divergence("stderr", _describe_bytes_diff(baseline.stderr, candidate.stderr)))

    if "written_files" in declared:
        b_keys, c_keys = set(baseline.written_files), set(candidate.written_files)
        for missing in sorted(b_keys - c_keys):
            out.append(Divergence("written_files", f"candidate did not write {missing}"))
        for extra in sorted(c_keys - b_keys):
            out.append(Divergence("written_files", f"candidate wrote unexpected {extra}"))
        for shared in sorted(b_keys & c_keys):
            if baseline.written_files[shared] != candidate.written_files[shared]:
                out.append(
                    Divergence(
                        "written_files",
                        f"{shared}: "
                        + _describe_bytes_diff(
                            baseline.written_files[shared], candidate.written_files[shared]
                        ),
                    )
                )
    return out


def _describe_bytes_diff(a: bytes, b: bytes) -> str:
    if len(a) != len(b):
        return f"length {len(a)} != {len(b)}"
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x != y:
            return f"first difference at byte {i}: 0x{x:02x} != 0x{y:02x}"
    return "identical"


class ByteEqualComparator:
    """Compression, serialisation, and anything with a wire format.

    The strictest discipline and the right default: if the contract says the bytes
    are the output, then different bytes are a different program.
    """

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract)
        return ComparisonResult(not divs, tuple(divs))


class RoundTripComparator:
    """Compression and codec families.

    Byte equality of the encoder output is checked *and* the decoder must recover
    the original input. A codec may legitimately produce different bytes after an
    optimization (a different but valid encoding) — but only if the package says
    so by declaring ``round_trip`` instead of ``byte_equal``. Round-trip identity
    is then the real contract, and it is the one this comparator enforces.
    """

    #: The package writes the recovered plaintext into state under this key.
    ROUND_TRIP_KEY = "round_trip_input"
    ORIGINAL_KEY = "original_input"

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract, stdout_equal=True)

        for label, obs in (("baseline", baseline), ("candidate", candidate)):
            original = obs.state.get(self.ORIGINAL_KEY)
            recovered = obs.state.get(self.ROUND_TRIP_KEY)
            if original is None or recovered is None:
                divs.append(
                    Divergence("round_trip", f"{label} did not report round-trip state")
                )
            elif original != recovered:
                divs.append(Divergence("round_trip", f"{label} failed to recover its own input"))

        return ComparisonResult(not divs, tuple(divs))


class FloatToleranceComparator:
    """Numerical and scientific kernels.

    The ULP or relative-error budget is part of the task contract, never the
    agent's choice. A patch that consumes the entire budget is still a pass, but
    the consumed fraction is reported so the customer-facing risk section can say
    how much headroom the optimization spent.
    """

    _NUM_RE = re.compile(rb"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract, stdout_equal=True)

        b_nums = [float(m.group()) for m in self._NUM_RE.finditer(baseline.stdout)]
        c_nums = [float(m.group()) for m in self._NUM_RE.finditer(candidate.stdout)]

        if len(b_nums) != len(c_nums):
            divs.append(
                Divergence("numeric", f"value count {len(b_nums)} != {len(c_nums)}")
            )
            return ComparisonResult(False, tuple(divs))

        # Non-numeric structure must still match exactly: only the numbers are
        # allowed to move within the budget.
        if self._NUM_RE.sub(b"#", baseline.stdout) != self._NUM_RE.sub(b"#", candidate.stdout):
            divs.append(Divergence("numeric", "non-numeric output structure changed"))

        for i, (b, c) in enumerate(zip(b_nums, c_nums, strict=True)):
            if math.isnan(b) or math.isnan(c):
                if math.isnan(b) != math.isnan(c):
                    divs.append(Divergence("numeric", f"value {i}: NaN mismatch"))
                continue
            if math.isinf(b) or math.isinf(c):
                if b != c:
                    divs.append(Divergence("numeric", f"value {i}: infinity mismatch {b} != {c}"))
                continue
            if not self._within_budget(b, c, contract):
                divs.append(
                    Divergence("numeric", f"value {i}: {b!r} vs {c!r} exceeds declared budget")
                )

        return ComparisonResult(not divs, tuple(divs))

    def _within_budget(self, b: float, c: float, contract: EquivalenceContract) -> bool:
        if contract.relative_error_budget is not None:
            denom = max(abs(b), 1e-300)
            return abs(b - c) / denom <= contract.relative_error_budget
        if contract.float_tolerance_ulp is not None:
            return _ulp_distance(b, c) <= contract.float_tolerance_ulp
        # A float discipline with no declared budget is a broken package. Fail
        # closed rather than silently accepting anything.
        return b == c

    @staticmethod
    def contract_is_complete(contract: EquivalenceContract) -> bool:
        return contract.relative_error_budget is not None or contract.float_tolerance_ulp is not None


def _ulp_distance(a: float, b: float) -> int:
    """Distance in representable doubles between two finite floats."""

    def to_ordered_int(x: float) -> int:
        bits = struct.unpack("<q", struct.pack("<d", x))[0]
        # Map the sign-magnitude float layout onto a monotone integer line.
        return bits if bits >= 0 else (1 << 63) - bits

    return abs(to_ordered_int(a) - to_ordered_int(b))


class StructuralComparator:
    """Parsing and text processing.

    Structural equality of the parse result, plus identical error positions and
    messages where the contract requires them. Whitespace inside the serialised
    form of the tree is allowed to move; the tree is not.
    """

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract, stdout_equal=True)

        b_tree, b_err = _parse_structural(baseline.stdout)
        c_tree, c_err = _parse_structural(candidate.stdout)

        if b_err or c_err:
            # A package declaring `structural` whose baseline does not emit
            # parseable output is misconfigured; say so rather than guessing.
            divs.append(
                Divergence("structural", f"unparseable output (baseline={b_err}, candidate={c_err})")
            )
        elif b_tree != c_tree:
            divs.append(Divergence("structural", _first_structural_diff(b_tree, c_tree)))

        return ComparisonResult(not divs, tuple(divs))


def _parse_structural(raw: bytes) -> tuple[object, str]:
    try:
        return json.loads(raw.decode()), ""
    except Exception as exc:  # noqa: BLE001 - the message is the diagnostic
        return None, str(exc)


def _first_structural_diff(a: object, b: object, path: str = "$") -> str:
    if type(a) is not type(b):
        return f"{path}: type {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k}: only in candidate"
            if k not in b:
                return f"{path}.{k}: only in baseline"
            if a[k] != b[k]:
                return _first_structural_diff(a[k], b[k], f"{path}.{k}")
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            if x != y:
                return _first_structural_diff(x, y, f"{path}[{i}]")
    return f"{path}: {a!r} != {b!r}"


class StateInvariantComparator:
    """Services and stateful systems.

    Compares declared state invariants and the observable transition sequence.
    Ordering is compared only where the original contract guarantees it, which is
    the distinction that keeps this from rejecting a legitimate parallelisation.
    """

    ORDERED_KEY = "ordered_transitions"
    UNORDERED_KEY = "unordered_transitions"
    INVARIANTS_KEY = "invariants"

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract, stdout_equal=True)

        b_inv = baseline.state.get(self.INVARIANTS_KEY, {})
        c_inv = candidate.state.get(self.INVARIANTS_KEY, {})
        if b_inv != c_inv:
            divs.append(Divergence("invariants", _first_structural_diff(b_inv, c_inv)))

        b_ord = baseline.state.get(self.ORDERED_KEY, [])
        c_ord = candidate.state.get(self.ORDERED_KEY, [])
        if b_ord != c_ord:
            divs.append(Divergence("ordered_transitions", _first_structural_diff(b_ord, c_ord)))

        # Where ordering is not guaranteed, compare as multisets.
        b_un = sorted(map(repr, baseline.state.get(self.UNORDERED_KEY, [])))
        c_un = sorted(map(repr, candidate.state.get(self.UNORDERED_KEY, [])))
        if b_un != c_un:
            divs.append(Divergence("unordered_transitions", "transition multiset differs"))

        return ComparisonResult(not divs, tuple(divs))


class OperationSequenceComparator:
    """Data structures and containers.

    Behavioural equivalence under a generated operation sequence, including
    iterator invalidation semantics — the part a naive container rewrite silently
    changes and a byte comparison never notices.
    """

    RESULTS_KEY = "operation_results"
    INVALIDATION_KEY = "iterator_invalidations"

    def compare(
        self, baseline: Observation, candidate: Observation, contract: EquivalenceContract
    ) -> ComparisonResult:
        divs = _compare_declared_channels(baseline, candidate, contract, stdout_equal=True)

        b_res = baseline.state.get(self.RESULTS_KEY)
        c_res = candidate.state.get(self.RESULTS_KEY)
        if b_res is None or c_res is None:
            divs.append(Divergence("operation_sequence", "operation results not reported"))
        elif b_res != c_res:
            divs.append(Divergence("operation_sequence", _first_structural_diff(b_res, c_res)))

        b_inv = baseline.state.get(self.INVALIDATION_KEY)
        c_inv = candidate.state.get(self.INVALIDATION_KEY)
        if b_inv != c_inv:
            divs.append(
                Divergence("iterator_invalidation", "iterator invalidation semantics changed")
            )

        return ComparisonResult(not divs, tuple(divs))


_REGISTRY: dict[EquivalenceDiscipline, Comparator] = {
    EquivalenceDiscipline.BYTE_EQUAL: ByteEqualComparator(),
    EquivalenceDiscipline.ROUND_TRIP: RoundTripComparator(),
    EquivalenceDiscipline.FLOAT_TOLERANCE: FloatToleranceComparator(),
    EquivalenceDiscipline.STRUCTURAL: StructuralComparator(),
    EquivalenceDiscipline.STATE_INVARIANT: StateInvariantComparator(),
    EquivalenceDiscipline.OPERATION_SEQUENCE: OperationSequenceComparator(),
}


def comparator_for(contract: EquivalenceContract) -> Comparator:
    """Resolve the comparator a task contract demands.

    Raises rather than falling back to a default: a task whose discipline cannot
    be resolved must void, never quietly get the lenient comparator.
    """
    if contract.discipline == EquivalenceDiscipline.FLOAT_TOLERANCE:
        if not FloatToleranceComparator.contract_is_complete(contract):
            raise ValueError(
                "float_tolerance discipline requires float_tolerance_ulp or relative_error_budget"
            )
    try:
        return _REGISTRY[contract.discipline]
    except KeyError as exc:
        raise ValueError(f"no comparator registered for {contract.discipline}") from exc


#: The sentence that must appear alongside every equivalence claim this module
#: produces, in both the audit bundle and the customer PR.
EVIDENCE_SCOPE_NOTICE = (
    "Equivalence evidence covers the declared side effects for the tested input "
    "distribution under the declared contract. This is evidence, not proof, and "
    "does not cover inputs outside that distribution."
)
