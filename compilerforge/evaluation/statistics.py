"""Robust statistics for the measurement tiers.

Two rules govern every measurement this network scores:

  "Robust statistics only: median, trimmed mean, bootstrap confidence intervals.
   Never a single run, never a bare mean."

  "Using the lower confidence bound rather than the point estimate means
   measurement uncertainty always costs the miner, never the network."

Every function here is deterministic given its inputs. The bootstrap uses a seeded
counter-based generator rather than ``random`` so two validators resampling the
same measurements reach the same confidence bound.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import numpy as np

from compilerforge.spec import SPEC


@dataclass(frozen=True, slots=True)
class Distribution:
    """A measured sample, summarised the way a reviewer needs to see it."""

    samples: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.samples:
            raise ValueError("a distribution needs at least one sample")

    @property
    def n(self) -> int:
        return len(self.samples)

    @property
    def median(self) -> float:
        return float(np.median(self.samples))

    @property
    def mean(self) -> float:
        return float(np.mean(self.samples))

    def trimmed_mean(self, proportion: float = 0.2) -> float:
        """Mean with the extreme tails removed, for skew from a stray scheduler event."""
        if self.n < 5:
            return self.median
        arr = np.sort(np.asarray(self.samples))
        cut = int(self.n * proportion / 2)
        kept = arr[cut : self.n - cut] if self.n - 2 * cut > 0 else arr
        return float(np.mean(kept))

    @property
    def stdev(self) -> float:
        return float(np.std(self.samples, ddof=1)) if self.n > 1 else 0.0

    @property
    def coefficient_of_variation(self) -> float:
        """The 2.7% figure that makes wall-clock an illegitimate consensus metric."""
        m = self.mean
        return self.stdev / abs(m) if m else math.inf

    @property
    def relative_spread(self) -> float:
        """(max - min) / median. Used for the Tier A stability gate."""
        lo, hi = min(self.samples), max(self.samples)
        med = self.median
        return (hi - lo) / abs(med) if med else math.inf

    def percentile(self, q: float) -> float:
        return float(np.percentile(self.samples, q))


def _seeded_generator(label: str, values: tuple[float, ...]) -> np.random.Generator:
    """A generator whose stream depends only on the label and the data.

    Two validators bootstrapping the same samples produce the same interval, which
    matters because the LCB feeds directly into the weight vector.
    """
    digest = hashlib.sha256(
        (label + "|" + ",".join(f"{v:.12g}" for v in values)).encode()
    ).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def bootstrap_ratio_lcb(
    numerator: Distribution,
    denominator: Distribution,
    *,
    confidence: float | None = None,
    resamples: int | None = None,
    label: str = "ratio",
) -> tuple[float, float, float]:
    """Lower bound, point estimate and upper bound for a speedup ratio.

    Speedup is baseline/candidate, a ratio of two independently measured
    distributions, so its uncertainty is not a simple function of either one's
    standard error. Bootstrapping both sides is the honest way to get an interval.

    Returns ``(lcb, point, ucb)``.
    """
    conf = confidence if confidence is not None else SPEC.capture.lcb_confidence
    n_resamples = resamples if resamples is not None else SPEC.capture.bootstrap_resamples

    num = np.asarray(numerator.samples, dtype=float)
    den = np.asarray(denominator.samples, dtype=float)
    point = float(np.median(num) / np.median(den)) if np.median(den) else 0.0

    if numerator.n < 2 or denominator.n < 2:
        # With a single measurement per side there is no interval to estimate.
        # Return the point estimate as its own bound and let the caller decide;
        # the stability gates upstream should already have rejected this.
        return point, point, point

    rng = _seeded_generator(label, numerator.samples + denominator.samples)
    num_draws = rng.choice(num, size=(n_resamples, numerator.n), replace=True)
    den_draws = rng.choice(den, size=(n_resamples, denominator.n), replace=True)
    ratios = np.median(num_draws, axis=1) / np.median(den_draws, axis=1)

    alpha = 1.0 - conf
    lcb = float(np.percentile(ratios, 100 * alpha / 2))
    ucb = float(np.percentile(ratios, 100 * (1 - alpha / 2)))
    return lcb, point, ucb


def bootstrap_mean_ci(
    dist: Distribution,
    *,
    confidence: float | None = None,
    resamples: int | None = None,
    label: str = "mean",
) -> tuple[float, float]:
    """Confidence interval on the median of one distribution."""
    conf = confidence if confidence is not None else SPEC.capture.lcb_confidence
    n_resamples = resamples if resamples is not None else SPEC.capture.bootstrap_resamples
    if dist.n < 2:
        return dist.median, dist.median

    rng = _seeded_generator(label, dist.samples)
    arr = np.asarray(dist.samples, dtype=float)
    draws = np.median(rng.choice(arr, size=(n_resamples, dist.n), replace=True), axis=1)
    alpha = 1.0 - conf
    return float(np.percentile(draws, 100 * alpha / 2)), float(
        np.percentile(draws, 100 * (1 - alpha / 2))
    )


def improvement_ci_excludes_zero(
    baseline: Distribution, candidate: Distribution, *, label: str = "improvement"
) -> tuple[bool, float]:
    """Does the change have a confidently non-zero direction?

    Used by the sign-agreement gate: Tier B only gets to contradict Tier A when it
    is confident, otherwise ordinary wall-clock noise would void healthy tasks.

    Returns ``(confident, signed_relative_change)`` where positive means faster.
    """
    lcb, point, ucb = bootstrap_ratio_lcb(baseline, candidate, label=label)
    confident = (lcb > 1.0) or (ucb < 1.0)
    return confident, point - 1.0


def standard_error_of_mean(values: list[float]) -> float:
    """SE used by the dethronement gap.

    Few samples demand a large gap; many samples relax the requirement toward the
    floor. That is the whole reason the required gap is a function of SE.
    """
    if len(values) < 2:
        return math.inf
    return float(np.std(values, ddof=1) / math.sqrt(len(values)))


def geometric_mean_of_one_plus(values: list[float]) -> float:
    """Aggregate in log space.

    ``geomean(1 + capture) - 1`` so one enormous win cannot mask repeated
    failures, and a zero-capture task drags the aggregate the way it should
    without annihilating it the way a plain geometric mean would.
    """
    if not values:
        return 0.0
    logs = [math.log1p(max(v, 0.0)) for v in values]
    return math.expm1(sum(logs) / len(logs))
