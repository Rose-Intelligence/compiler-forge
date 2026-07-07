"""The signed validator score artifact (Appendix A.2).

Every field a third party needs to re-run the round is present. This object is
what gets published to the public audit repository after each round,
and it is the credibility argument the commercial product actually sells.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from compilerforge.spec import SPEC, SPEC_VERSION


class GateName(StrEnum):
    """Every stage before measurement is a hard gate."""

    ARTIFACT_INTEGRITY = "artifact_integrity"
    INTERFACE = "interface"
    PATCH_APPLIES = "patch_applies"
    BUILD = "build"
    API_ABI = "api_abi"
    TEST_INVENTORY = "test_inventory"
    PUBLIC_TESTS = "public_tests"
    DIFFERENTIAL = "differential"
    FUZZ = "fuzz"
    ASAN = "asan"
    UBSAN = "ubsan"
    SECOND_OPT_LEVEL = "second_opt_level"
    PATCH_HYGIENE = "patch_hygiene"
    BASELINE_STABLE = "baseline_stable"


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: GateName
    passed: bool
    detail: str = ""
    #: Wall seconds the gate itself consumed, for the validator cost model.
    seconds: float = 0.0


class TierAResult(BaseModel):
    """Deterministic cost — the consensus-bearing tier."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instructions_baseline: int
    instructions_candidate: int
    deterministic_speedup: float
    #: Repeated Callgrind runs; on a simulated CPU these should be identical.
    speedup_samples: tuple[float, ...] = ()
    speedup_lcb: float = 0.0
    d1_miss_delta_pct: float | None = None
    ll_miss_delta_pct: float | None = None
    estimated_cycles_baseline: int | None = None
    estimated_cycles_candidate: int | None = None


class TierBResult(BaseModel):
    """Wall-clock reality — reporting and gating, never consensus ranking."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    median_speedup: float
    speedup_lcb_95: float
    p95_improvement_pct: float | None = None
    p99_improvement_pct: float | None = None
    peak_rss_improvement_pct: float | None = None
    compile_time_change_pct: float | None = None
    #: Tier A and Tier B agree on the direction of the change.
    sign_agreement: bool = True
    #: Set when the calibrated host drifted; the window is rejected, not corrected.
    host_drift_detected: bool = False
    measured_runs: int = 0


class ReferenceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    s_ref: float
    capture: float


class ScoreArtifact(BaseModel):
    """One (artifact, task) evaluation by one validator.

    Scores are only comparable within an identical
    (spec_version, spec_digest, toolchain_digest, corpus_snapshot, hardware_class)
    tuple. ``comparable_with`` enforces that; validators must refuse to mix.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = SPEC_VERSION
    spec_digest: str = Field(default_factory=SPEC.digest)
    artifact_digest: str
    task_id: str
    toolchain_digest: str
    corpus_snapshot: str
    hardware_class: str = SPEC.hardware_class

    #: Who executed the agent — production happens once per pair...
    producer_uid: int | None = None
    #: ...and who measured the resulting patch. Independence lives here.
    verifier_hotkey: str = ""

    gates: list[GateResult] = Field(default_factory=list)
    tier_a: TierAResult | None = None
    tier_b: TierBResult | None = None
    reference: ReferenceResult | None = None

    #: Final per-task score in [0, 1]-ish space. Zero whenever any gate failed.
    score: float = 0.0
    #: An honest empty result: every gate passed, no improvement claimed.
    honest_null: bool = False
    #: Set when the task was voided for everyone (unstable baseline, sign
    #: divergence). Voided tasks are excluded from aggregation entirely.
    voided: bool = False
    void_reason: str = ""

    patch_digest: str | None = None
    validator_signature: str = ""

    def all_gates_passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def failed_gate(self) -> GateName | None:
        for g in self.gates:
            if not g.passed:
                return g.name
        return None

    def comparability_key(self) -> tuple[int, str, str, str, str]:
        return (
            self.spec_version,
            self.spec_digest,
            self.toolchain_digest,
            self.corpus_snapshot,
            self.hardware_class,
        )

    def comparable_with(self, other: ScoreArtifact) -> bool:
        return self.comparability_key() == other.comparability_key()

    def signing_payload(self) -> bytes:
        """Bytes that the verifier hotkey signs.

        The signature covers everything except itself, so a third party can verify
        the artifact was not edited after the validator committed to it.
        """
        body = self.model_dump(mode="json", exclude={"validator_signature"})
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def content_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.signing_payload()).hexdigest()
