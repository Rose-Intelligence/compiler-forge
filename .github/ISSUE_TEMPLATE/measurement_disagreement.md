---
name: Measurement disagreement
about: Two validators measured the same patch differently
title: "measurement: "
labels: consensus, measurement
---

<!--
This is the highest-priority class of bug in the project. The deterministic tier
runs on a simulated CPU precisely so that independent validators agree; when
they do not, either a task package is non-deterministic or the measurement code
has drifted, and both are worth stopping for.
-->

## The disagreement

| | Validator A | Validator B |
|---|---|---|
| hotkey | | |
| instruction count | | |
| deterministic speedup | | |

**Relative difference:**
<!-- The published tolerance is spec.measurement.tier_a_cross_validator_tolerance -->

## Comparability tuple

Scores are only comparable within an identical tuple. Please confirm both sides
match before reporting — a mismatch here is a configuration problem rather than
a measurement bug.

| | Validator A | Validator B |
|---|---|---|
| `spec_digest` | | |
| `toolchain_digest` | | |
| `corpus_snapshot` | | |
| `hardware_class` | | |

<!-- cf-eval spec prints the first; the score artifact carries all four. -->

## Task

- Package:
- Workload profile:
- Task id:
- Round / block:

## Evidence

<!-- The two signed score artifacts, or the round.json entries containing them. -->

```json
```

## Reproduction

<!-- Ideally: the command that reproduces the disagreement on a third host. -->

```bash
cf-eval patch corpus/<package> --patch <patch> --seed <seed>
```
