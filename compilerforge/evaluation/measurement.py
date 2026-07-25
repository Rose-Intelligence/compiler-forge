"""Stage 5 — the two-tier measurement protocol.

The central mechanism-design claim of this subnet:

    Wall-clock is a legitimate business metric and an illegitimate consensus
    metric.

Real benchmark suites on commodity CI runners show a coefficient of variation
around 2.7%. Cachegrind instruction counting over the same program varies by
roughly a millionth of a percent. If a permissionless validator set ranked miners
by wall-clock, two honest validators measuring a genuine 3% improvement would
frequently disagree about its *sign*, their weight vectors would diverge, Yuma
clipping would punish them, and validator trust would decay for reasons unrelated
to dishonesty.

So:

  Tier A  Callgrind on a simulated CPU        -> the number that enters the weight vector
  Tier B  wall-clock on a calibrated host     -> reporting, plus a sign-agreement gate

Tier B never ranks. It only gets to say "the deterministic tier claims this is
faster, and on real hardware it is confidently slower" — which catches the
interesting adversarial case of an optimization that cuts instruction count while
destroying memory-level parallelism or branch prediction.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from compilerforge.evaluation.statistics import (
    Distribution,
    bootstrap_ratio_lcb,
    improvement_ci_excludes_zero,
)
from compilerforge.protocol.score import TierAResult, TierBResult
from compilerforge.spec import SPEC


class MeasurementError(RuntimeError):
    """A measurement that cannot be trusted. Never a guessed number."""


# ---------------------------------------------------------------------------
# Tier A — deterministic cost
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallgrindCounters:
    """Raw event totals from one Callgrind run."""

    ir: int  # instruction reads — the primary quantity
    dr: int = 0  # data reads
    dw: int = 0  # data writes
    i1mr: int = 0  # L1 instruction cache misses
    d1mr: int = 0  # L1 data read misses
    d1mw: int = 0  # L1 data write misses
    ilmr: int = 0  # last-level instruction misses
    dlmr: int = 0  # last-level data read misses
    dlmw: int = 0  # last-level data write misses

    @property
    def l1_misses(self) -> int:
        return self.i1mr + self.d1mr + self.d1mw

    @property
    def ll_misses(self) -> int:
        return self.ilmr + self.dlmr + self.dlmw

    @property
    def estimated_cycles(self) -> int:
        """Cachegrind's published cost model.

        A last-level miss is charged 100 cycles and an L1 miss 10, which is the
        same weighting ``cg_annotate`` uses. It is a model, not a measurement —
        which is exactly why it informs the report and does not replace Tier B.
        """
        return self.ir + 10 * self.l1_misses + 100 * self.ll_misses


#: Callgrind writes an `events:` line naming the counters, and a cost line whose
#: fields correspond to it positionally. Which cost line carries the real numbers
#: depends on the Valgrind version and on whether instrumentation was gated:
#: on 3.22, running with --instr-atstart=no leaves `summary:` at a single zero
#: and puts the bracketed cost in `totals:`, while other versions populate
#: `summary:`. Rather than hard-coding either, both are parsed and the one that
#: actually carries a full set of non-zero counters is used.
#:
#: Parsing these two lines is still far more robust than shelling out to
#: callgrind_annotate and scraping its human-facing table.
_EVENTS_RE = re.compile(r"^events:\s*(.+)$", re.MULTILINE)
_COST_LINE_RE = re.compile(r"^(summary|totals):\s*(.+)$", re.MULTILINE)


def parse_callgrind_output(text: str) -> CallgrindCounters:
    """Parse a callgrind.out file into event totals."""
    events_match = _EVENTS_RE.search(text)
    if not events_match:
        raise MeasurementError("callgrind output has no events line")

    names = events_match.group(1).split()

    best: dict[str, int] | None = None
    for _label, raw in _COST_LINE_RE.findall(text):
        try:
            values = [int(v) for v in raw.split()]
        except ValueError:
            continue
        if len(values) < len(names):
            # A truncated cost line — most often `summary: 0` — carries no
            # usable information. Skip it rather than reading a zero as a
            # measurement.
            continue
        candidate = dict(zip(names, values, strict=False))
        if candidate.get("Ir", 0) <= 0:
            continue
        if best is None or candidate["Ir"] > best["Ir"]:
            best = candidate

    if best is None:
        raise MeasurementError(
            "callgrind reported no instructions. Either the benchmark never "
            "reached its measured region, or the binary was built without the "
            "instrumentation markers the task contract declares."
        )
    table = best

    def get(*keys: str) -> int:
        for k in keys:
            if k in table:
                return table[k]
        return 0

    return CallgrindCounters(
        ir=get("Ir"),
        dr=get("Dr"),
        dw=get("Dw"),
        i1mr=get("I1mr"),
        d1mr=get("D1mr"),
        d1mw=get("D1mw"),
        ilmr=get("ILmr", "I2mr"),
        dlmr=get("DLmr", "D2mr"),
        dlmw=get("DLmw", "D2mw"),
    )


@dataclass(slots=True)
class TierARunner:
    """Deterministic cost measurement under Valgrind.

    ``--instr-atstart=no`` plus explicit client requests around the measured
    region means startup, argument parsing and teardown are not counted. Measuring
    them would let a miner "optimize" by moving work out of the region, and it
    would swamp a small kernel's real cost with process setup.
    """

    valgrind_path: str = "valgrind"
    #: Callgrind carries a 10-50x slowdown, which is why Stage 5a runs only on
    #: candidates that already survived Stages 2-4.
    timeout_seconds: int = 3600
    cache_simulation: bool = True
    #: When the binary has no cf_bench_start/cf_bench_stop markers, instrument the
    #: whole process. The package declares which it is; we never guess silently.
    instrument_at_start: bool = False

    def available(self) -> bool:
        return shutil.which(self.valgrind_path) is not None

    def measure(
        self,
        command: str,
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        repeats: int | None = None,
        instrument_at_start: bool | None = None,
    ) -> list[CallgrindCounters]:
        """Run the benchmark under Callgrind ``repeats`` times.

        ``instrument_at_start`` comes from the task's benchmark contract: a
        package whose benchmark carries the markers is measured between them, and
        one that cannot carry them is measured whole. Passing it per call rather
        than per runner keeps a single runner correct across a round in which
        different packages declare different modes.

        On a simulated CPU these results should be identical. Repeating is a
        cheap detector for a benchmark that is secretly non-deterministic — one
        that reads the clock, the PID, or /dev/urandom — which would silently
        destroy cross-validator agreement if it reached the weight vector.
        """
        at_start = (
            self.instrument_at_start if instrument_at_start is None else instrument_at_start
        )
        if not self.available():
            raise MeasurementError(
                "valgrind is not installed; Tier A is the consensus-bearing tier and "
                "a validator without it must not score (fail closed)"
            )

        n = repeats if repeats is not None else SPEC.measurement.tier_a_repeats
        results: list[CallgrindCounters] = []

        for i in range(n):
            with tempfile.TemporaryDirectory() as tmp:
                # %p is not optional with --trace-children: without it every
                # traced process writes to the same path and the last one to
                # exit — usually the shell, which was never instrumented —
                # clobbers the benchmark's profile with an empty one.
                out_file = Path(tmp) / f"callgrind.out.{i}.%p"
                args = [
                    self.valgrind_path,
                    "--tool=callgrind",
                    f"--callgrind-out-file={out_file}",
                    f"--instr-atstart={'yes' if at_start else 'no'}",
                    f"--cache-sim={'yes' if self.cache_simulation else 'no'}",
                    "--branch-sim=no",
                    # Children are part of the program's cost; a benchmark that
                    # forks its real work would otherwise measure as free.
                    "--trace-children=yes",
                    "--",
                    "/bin/sh",
                    "-c",
                    command,
                ]
                proc = subprocess.run(  # noqa: S603 - validator-owned command, not miner input
                    args,
                    cwd=cwd,
                    env={**os.environ, **(env or {})},
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                if proc.returncode != 0:
                    raise MeasurementError(
                        f"benchmark exited {proc.returncode} under callgrind: "
                        f"{proc.stderr.strip()[-500:]}"
                    )
                candidates = sorted(Path(tmp).glob("callgrind.out*"))
                if not candidates:
                    raise MeasurementError("callgrind produced no output file")

                # --trace-children emits one file per process, so the shell that
                # launched the benchmark gets its own. That file legitimately
                # reports nothing — instrumentation is off outside the measured
                # region — and must be skipped rather than treated as a failed
                # measurement. Only the absence of *any* instrumented process is
                # an error.
                totals: list[CallgrindCounters] = []
                for path in candidates:
                    try:
                        totals.append(parse_callgrind_output(path.read_text(errors="replace")))
                    except MeasurementError:
                        continue

                if not totals:
                    raise MeasurementError(
                        "callgrind reported no instructions in any traced process. Either "
                        "the benchmark never reached its measured region, or the binary "
                        "was built without the instrumentation markers the task contract "
                        f"declares ({command!r})."
                    )
                results.append(_sum_counters(totals))

        return results

    def compare(
        self,
        baseline: list[CallgrindCounters],
        candidate: list[CallgrindCounters],
    ) -> TierAResult:
        """Turn two sets of counters into the consensus-bearing result."""
        if not baseline or not candidate:
            raise MeasurementError("Tier A comparison needs measurements on both sides")

        base_ir = Distribution(tuple(float(c.ir) for c in baseline))
        cand_ir = Distribution(tuple(float(c.ir) for c in candidate))

        for label, dist in (("baseline", base_ir), ("candidate", cand_ir)):
            spread = dist.relative_spread
            if spread > SPEC.stability.max_tier_a_relative_spread:
                # On a simulated CPU this is a non-deterministic benchmark, not noise.
                raise MeasurementError(
                    f"Tier A {label} varies by {spread:.6%} across repeats, above the "
                    f"{SPEC.stability.max_tier_a_relative_spread:.6%} tolerance; "
                    "the benchmark is not deterministic"
                )

        speedups = tuple(base_ir.median / c for c in cand_ir.samples if c > 0)
        lcb, point, _ = bootstrap_ratio_lcb(base_ir, cand_ir, label="tier-a")

        base_med = _median_counters(baseline)
        cand_med = _median_counters(candidate)

        return TierAResult(
            instructions_baseline=base_med.ir,
            instructions_candidate=cand_med.ir,
            deterministic_speedup=point,
            speedup_samples=speedups,
            # With identical repeats the bootstrap collapses to the point estimate,
            # which is the correct behaviour: there is no uncertainty to charge for.
            speedup_lcb=min(lcb, point),
            d1_miss_delta_pct=_pct_delta(base_med.l1_misses, cand_med.l1_misses),
            ll_miss_delta_pct=_pct_delta(base_med.ll_misses, cand_med.ll_misses),
            estimated_cycles_baseline=base_med.estimated_cycles,
            estimated_cycles_candidate=cand_med.estimated_cycles,
        )


def _sum_counters(items: list[CallgrindCounters]) -> CallgrindCounters:
    return CallgrindCounters(
        ir=sum(c.ir for c in items),
        dr=sum(c.dr for c in items),
        dw=sum(c.dw for c in items),
        i1mr=sum(c.i1mr for c in items),
        d1mr=sum(c.d1mr for c in items),
        d1mw=sum(c.d1mw for c in items),
        ilmr=sum(c.ilmr for c in items),
        dlmr=sum(c.dlmr for c in items),
        dlmw=sum(c.dlmw for c in items),
    )


def _median_counters(items: list[CallgrindCounters]) -> CallgrindCounters:
    import numpy as np

    def med(attr: str) -> int:
        return int(np.median([getattr(c, attr) for c in items]))

    return CallgrindCounters(
        ir=med("ir"),
        dr=med("dr"),
        dw=med("dw"),
        i1mr=med("i1mr"),
        d1mr=med("d1mr"),
        d1mw=med("d1mw"),
        ilmr=med("ilmr"),
        dlmr=med("dlmr"),
        dlmw=med("dlmw"),
    )


def _pct_delta(baseline: int, candidate: int) -> float | None:
    if baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100.0


# ---------------------------------------------------------------------------
# Tier B — wall-clock reality
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HostCalibration:
    """What must be true of the machine before Tier B means anything.

    Tier B on a shared or virtualised host is not a weaker measurement; it is a
    different measurement of a different thing. ``assert_calibrated`` refuses
    rather than reporting a number with a caveat nobody reads.
    """

    governor_pinned: bool
    turbo_disabled: bool
    cores_isolated: bool
    aslr_disabled_for_process: bool
    numa_pinned: bool
    thermal_monitoring: bool

    @classmethod
    def probe(cls) -> HostCalibration:
        """Best-effort inspection of the current host."""
        governor = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        no_turbo = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
        cmdline = Path("/proc/cmdline")
        return cls(
            governor_pinned=governor.exists()
            and governor.read_text(errors="ignore").strip() == "performance",
            turbo_disabled=no_turbo.exists() and no_turbo.read_text(errors="ignore").strip() == "1",
            cores_isolated=cmdline.exists() and "isolcpus=" in cmdline.read_text(errors="ignore"),
            # Set per measured process via setarch, so we assume the runner does it.
            aslr_disabled_for_process=True,
            numa_pinned=shutil.which("numactl") is not None,
            thermal_monitoring=Path("/sys/class/thermal").exists(),
        )

    def problems(self) -> list[str]:
        out = []
        if not self.governor_pinned:
            out.append("CPU frequency governor is not pinned to 'performance'")
        if not self.turbo_disabled:
            out.append("turbo boost is not disabled")
        if not self.cores_isolated:
            out.append("no isolated cores (isolcpus=) on the kernel command line")
        if not self.numa_pinned:
            out.append("numactl unavailable; NUMA placement cannot be fixed")
        return out

    def assert_calibrated(self) -> None:
        problems = self.problems()
        if problems:
            raise MeasurementError(
                "calibrated host requirements unmet: " + "; ".join(problems)
            )


@dataclass(slots=True)
class TierBRunner:
    """Wall-clock measurement on the calibrated bare-metal host.

    Randomised baseline/candidate ordering is not a nicety: if all baseline runs
    happen first, any thermal or cache drift over the measurement window maps
    directly onto the reported speedup.
    """

    warmup_runs: int = SPEC.measurement.tier_b_warmup_runs
    measured_runs: int = SPEC.measurement.tier_b_measured_runs
    randomise_order: bool = SPEC.measurement.tier_b_randomise_order
    timeout_seconds: int = 1800
    #: Pin the measured process to specific cores when the host provides them.
    cpu_affinity: str | None = None
    require_calibration: bool = True
    _thermal_baseline: float | None = field(default=None, init=False)

    def measure_pair(
        self,
        baseline_command: str,
        candidate_command: str,
        *,
        baseline_cwd: Path,
        candidate_cwd: Path,
        seed: int = 0,
    ) -> tuple[Distribution, Distribution, bool]:
        """Run both sides interleaved in a deterministic pseudo-random order.

        Returns ``(baseline, candidate, host_drift_detected)``.
        """
        if self.require_calibration:
            HostCalibration.probe().assert_calibrated()

        for _ in range(self.warmup_runs):
            self._time_once(baseline_command, baseline_cwd)
            self._time_once(candidate_command, candidate_cwd)

        self._thermal_baseline = _read_max_temperature()

        order = self._interleave(seed)
        base_samples: list[float] = []
        cand_samples: list[float] = []
        for is_candidate in order:
            if is_candidate:
                cand_samples.append(self._time_once(candidate_command, candidate_cwd))
            else:
                base_samples.append(self._time_once(baseline_command, baseline_cwd))

        drift = self._thermal_drift_detected()
        return Distribution(tuple(base_samples)), Distribution(tuple(cand_samples)), drift

    def _interleave(self, seed: int) -> list[bool]:
        """A balanced, deterministically shuffled run order."""
        import hashlib

        order = [i % 2 == 1 for i in range(self.measured_runs * 2)]
        if not self.randomise_order:
            return order
        state = seed
        for i in range(len(order) - 1, 0, -1):
            state = int.from_bytes(hashlib.sha256(str(state).encode()).digest(), "big")
            j = state % (i + 1)
            order[i], order[j] = order[j], order[i]
        return order

    def _time_once(self, command: str, cwd: Path) -> float:
        args: list[str] = []
        if self.cpu_affinity:
            args += ["taskset", "-c", self.cpu_affinity]
        # Disable address-space randomisation for the measured process so layout
        # luck does not show up as a performance difference.
        if shutil.which("setarch"):
            args += ["setarch", os.uname().machine, "-R"]
        args += ["/bin/sh", "-c", command]

        started = time.perf_counter()
        proc = subprocess.run(  # noqa: S603 - validator-owned command
            args, cwd=cwd, capture_output=True, text=True, timeout=self.timeout_seconds, check=False
        )
        elapsed = time.perf_counter() - started
        if proc.returncode != 0:
            raise MeasurementError(
                f"benchmark exited {proc.returncode} during Tier B: {proc.stderr.strip()[-300:]}"
            )
        return elapsed

    def _thermal_drift_detected(self, threshold_celsius: float = 5.0) -> bool:
        """Reject a measurement window rather than correcting it."""
        if self._thermal_baseline is None:
            return False
        current = _read_max_temperature()
        if current is None:
            return False
        return (current - self._thermal_baseline) > threshold_celsius

    def compare(
        self,
        baseline: Distribution,
        candidate: Distribution,
        *,
        host_drift: bool = False,
        peak_rss_baseline: int | None = None,
        peak_rss_candidate: int | None = None,
        compile_seconds_baseline: float | None = None,
        compile_seconds_candidate: float | None = None,
    ) -> TierBResult:
        cov = candidate.coefficient_of_variation
        if cov > SPEC.stability.max_tier_b_coefficient_of_variation:
            # Not fatal by itself — the LCB will absorb it — but it is recorded so
            # a validator whose host is quietly degrading is visible in the audit.
            pass

        lcb, point, _ = bootstrap_ratio_lcb(baseline, candidate, label="tier-b")
        return TierBResult(
            median_speedup=point,
            speedup_lcb_95=lcb,
            p95_improvement_pct=_improvement_pct(baseline.percentile(95), candidate.percentile(95)),
            p99_improvement_pct=_improvement_pct(baseline.percentile(99), candidate.percentile(99)),
            peak_rss_improvement_pct=_improvement_pct(peak_rss_baseline, peak_rss_candidate),
            compile_time_change_pct=_change_pct(
                compile_seconds_baseline, compile_seconds_candidate
            ),
            sign_agreement=True,  # set by check_sign_agreement
            host_drift_detected=host_drift,
            measured_runs=candidate.n,
        )


def _improvement_pct(baseline: float | int | None, candidate: float | int | None) -> float | None:
    """Percent reduction. Positive means the candidate uses less."""
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (baseline - candidate) / baseline * 100.0


def _change_pct(baseline: float | None, candidate: float | None) -> float | None:
    """Percent change. Positive means the candidate takes longer."""
    if baseline is None or candidate is None or baseline == 0:
        return None
    return (candidate - baseline) / baseline * 100.0


def _read_max_temperature() -> float | None:
    root = Path("/sys/class/thermal")
    if not root.exists():
        return None
    temps: list[float] = []
    for zone in root.glob("thermal_zone*/temp"):
        try:
            temps.append(int(zone.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return max(temps) if temps else None


# ---------------------------------------------------------------------------
# How the tiers combine
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignAgreement:
    agreed: bool
    should_rerun: bool
    should_void: bool
    detail: str


def check_sign_agreement(
    tier_a: TierAResult,
    baseline: Distribution,
    candidate: Distribution,
    *,
    attempt: int = 0,
) -> SignAgreement:
    """The gate that catches an instruction-count win that is not a real win.

    Tier A says faster. If the calibrated host says *confidently* slower, the task
    is flagged and re-run; persistent divergence voids the task and opens an
    investigation. Ordinary wall-clock noise is not enough to contradict Tier A —
    the interval must exclude zero — otherwise 2.7% CV would void healthy rounds.
    """
    a_says_faster = tier_a.deterministic_speedup > 1.0
    confident, relative_change = improvement_ci_excludes_zero(baseline, candidate)
    b_says_faster = relative_change > 0

    if not confident:
        return SignAgreement(
            agreed=True,
            should_rerun=False,
            should_void=False,
            detail="Tier B interval includes zero; insufficient evidence to contradict Tier A",
        )

    if a_says_faster == b_says_faster:
        return SignAgreement(True, False, False, "tiers agree on direction")

    detail = (
        f"Tier A reports {tier_a.deterministic_speedup:.4f}x "
        f"({'faster' if a_says_faster else 'slower'}) but the calibrated host reports "
        f"{relative_change:+.2%} with an interval excluding zero"
    )
    if attempt < SPEC.measurement.sign_agreement_reruns:
        return SignAgreement(False, True, False, detail + " — re-running")
    return SignAgreement(False, False, True, detail + " — persistent divergence, task voided")
