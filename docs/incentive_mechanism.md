# Incentive mechanism

How a measurement becomes a weight.

Everything here is defined by constants in [`compilerforge/spec.py`](../compilerforge/spec.py).
Changing any of them changes the consensus digest, which makes scores from before
and after incomparable — deliberately.

---

## The commodity being paid for

> A reusable, immutable optimization agent that transforms a supplied repository
> under a fixed build, test, benchmark, hardware and resource contract, returning
> patches that preserve required behaviour and materially reduce measured cost.

Two weak designs this definition rules out. It does not pay for isolated patches
that never compound into anything — the failure mode of a bounty board. And it
does not pay for a generic coding contest in which the largest private model wins
and the optimization objective quietly becomes secondary.

---

## Hard gates before any score exists

| Gate | Failure result |
|------|---------------|
| Artifact integrity, interface conformance | No evaluation. Zero for the round |
| Build, API/ABI conformance | Zero for the task |
| Differential behaviour on hidden inputs | Zero for the task, plus a reliability penalty |
| Fuzzing and sanitizer findings | Zero for the task, plus a reliability penalty |
| Baseline stability | **Task voided for every miner** and re-run. No crown change |
| Minimum improvement threshold | No positive reward. Not a penalty — an honest null result |

The distinction in the last two rows matters. A voided task is a problem with the
task, and zeroing whichever miner happened to be evaluated when the instability
surfaced would be arbitrary. An honest null is a legitimate outcome that still
earns from the floor pool.

---

## The per-task primitive: capture

Raw speedup is a poor primitive. It is unbounded, so one lucky repository
dominates. It is incomparable across tasks of different difficulty. And it says
nothing about how much of the *available* headroom was captured.

Every task carries a **reference optimization** — the human expert commit where
licensing permits, otherwise a curated expert patch — measured once at corpus
build time and frozen.

```
S_ref(t)    deterministic speedup of the reference patch    (fixed per task)
S_lcb(a,t)  lower confidence bound of the artifact speedup  (Tier A)

capture(a,t) = clamp( (S_lcb − 1) / (S_ref − 1),  0,  2.0 )
```

| capture | Meaning |
|---------|---------|
| 0.0 | No credible improvement |
| 1.0 | Matched the human expert |
| > 1.0 | Beat the expert, capped at 2.0 |

Three properties earn this its place:

**Bounded.** No single repository can carry an artifact.

**Comparable across difficulty.** Normalised by achievable headroom rather than
absolute time, so half of a 20% opportunity scores the same as half of a 200% one.
This is asserted directly in `tests/test_scoring.py`.

**Contamination-resistant.** An artifact consistently above 1.0 has demonstrably
generalised beyond any memorised commit.

### Why a lower bound

`S_lcb`, not the point estimate. Measurement uncertainty always costs the miner
and never the network — the correct direction for an incentive system to be wrong
in. A noisy 1.20x scores below a confident 1.15x.

### Why per workload profile

The same reference patch is routinely worth several times more on one workload
shape than another. In the shipped corpus, `token-count`'s reference is 4.05x on a
small vocabulary and 10.30x on a large one — the same patch, the same code.

So `S_ref` is stored **per profile**, not per package. A package-wide average
would over-reward artifacts on the easy profiles and under-reward them on the hard
ones, and the round draws its profile from the block hash.

---

## Score components

| Component | Weight | Measured by | Why it is there |
|-----------|--------|-------------|-----------------|
| Deterministic cost capture | 55% | Tier A | The primary economic signal, consensus-bearing |
| Peak memory reduction | 15% | Peak RSS, calibrated host | Workload density, container sizing, edge deployment |
| Tail-latency improvement | 10% | Tier B p95/p99 | Blocks average-speed wins that worsen production tails |
| Hidden-family generalisation | 10% | Held-out packages | Discourages corpus-specific hacks |
| Compile and build-time impact | 5% | Build wall-clock | Prevents template explosions that trade developer time for runtime |
| Cross-validator agreement | 5% | Tier A variance across validators | Rewards gains that are reproducible, not environment-specific |

A component with no evidence contributes **nothing**, and the remainder is *not*
renormalised. A validator without a calibrated host reports a smaller number
rather than silently inflating the components it does have.

Energy is reported but not scored, and will stay that way until validators can
calibrate power counters consistently.

Maintainability and security are expressed as **eligibility rules and explicit
penalties** — patch size caps, complexity limits, portability checks — never as
subjective positive points. A subjective positive component is an attack surface.

---

## Cross-task aggregation

Aggregated in log space: the geometric mean of `1 + capture` across every task in
the round.

This is why one enormous win cannot mask repeated failures. Four tasks at 0.5
capture beat one at 2.0 and three at zero — verified in the test suite.

Generalist and per-cell specialist scores are published separately, so an artifact
cannot take the generalist crown by dominating a single library family.

---

## Dethronement

The commercial deployment path uses exactly one default agent, so the mechanism
selects exactly one — with a meaningful margin and a short memory.

**Dominance band.** The challenger must exceed the champion by margin `m = 0.04`
on aggregate **and** must not be materially worse on any scored cell, within
tolerance `τ = 0.02`. Without the per-cell clause, a better average could hide a
regression in one workload family.

**A statistical margin that shrinks with evidence.**

```
required gap = clamp(z · SE, 0.01, 0.25)
```

Few samples demand a large gap; many samples relax toward the floor. This is what
makes a near-copy of the champion unable to dethrone it — a copy matches on shared
tasks and can never clear a positive margin everywhere. The test suite runs a
challenger scoring 1% higher for ten consecutive rounds and confirms the crown
does not move.

**Defender advantage.** The crown changes only after the challenger clears the
band for `warmup_rounds = 3` consecutive rounds, never on a single round, and
never on a round whose baseline stability gate fired.

**Tie-break by commitment order.** The earlier on-chain commitment wins, which
removes any payoff from watching the leaderboard and cloning whoever is ahead.

Specialist cells use **top-K** rather than a single champion, because multiple
deployment targets are simultaneously useful — the best agent legitimately differs
by language, workload and hardware, and a single universal champion averages that
away.

---

## Emission structure

Two chain-level mechanisms, each running Yuma Consensus independently with its own
weight matrix and bond pools. Miners keep a single UID across both, so one
artifact can hold the generalist crown and earn in a specialist cell without any
custom accounting.

| Pool | Share | Mechanism |
|------|-------|-----------|
| Generalist Agent Championship | 60% | 0 |
| Specialist cells | 25% | 1 |
| Open-source performance bounties | 15% | 1 |

Split as u16 weights summing to 65,535: `[39321, 26214]`.

> **Constraint:** `max_allowed_uids × mechanism_count ≤ 256`. A two-mechanism
> subnet must run at most 128 UIDs.

---

## The burn policy, and why it is a floor instead

This is the part most likely to be got wrong by a subnet copying an older design.

Each subnet's share of block emission is:

```
share_i = p_i × (1 − b_i) / Σ_j p_j × (1 − b_j)
```

where `b_i` is the proportion of the last tempo's miner incentive withheld because
it was directed to owner hotkeys — **counted whether that alpha was recycled or
burned**.

A subnet that burns 80% of miner incentive therefore takes roughly an 80% cut to
its own TAO inflow, on top of paying nothing to miners. "Burn the cell when nobody
beats the baseline" is not conservative; it is self-harming.

There is a further wrinkle: if an epoch ends with zero total miner incentive, the
miner half of that tempo's pending alpha is paid to **validators** rather than
withheld. A subnet paying nothing to miners is not saving anything — it is quietly
transferring the miner half to validators while cutting its own inflow.

So, four rules:

**1. Floor, do not burn.** When no artifact clears the improvement threshold,
emission is distributed across artifacts that passed every correctness gate and
returned an honest null result, weighted by reliability history. This pays for
verified correct work — which has real value — and keeps `b_i` near zero.

**2. Decay the champion instead.** A champion unbeaten and unimproved for 14
rounds sees its share decay 5% per round toward a floor of 35%. The decayed
remainder joins the floor pool; it is never redirected to an owner hotkey.

**3. Raise the bar, do not close the tap.** If the network stops improving, the
answer is a harder corpus refresh.

**4. Burn is a safety valve.** Retained for a discovered exploit, a corrupted
corpus or a security incident, disabled by default, and every activation is logged
with its published reason. An incident, not a mechanism.

### Reliability history

An artifact that fails a differential or sanitizer gate takes a reliability
penalty. Reliability weights the floor distribution, and after three correctness
failures an artifact leaves the floor pool entirely.

---

## Anti-gaming

| Attack | Mitigation | Cost to detect |
|--------|-----------|----------------|
| Hard-coded outputs for known inputs | Future-seeded hidden inputs, timelocked test material, artifact size caps | Free — structural |
| Fetching the upstream fix commit | No external network during authoritative runs | Free — structural |
| Deleting or weakening tests | Validator-owned tests mounted read-only; inventory verified by hash | Low |
| Benchmark-specific special-casing | Hidden workload sizes, procedurally generated inputs | Medium |
| Exploiting undefined behaviour | ASan and UBSan as hard gates, plus a rebuild at a second optimization level | Medium |
| Measurement manipulation | Validator-owned counters; the agent never reports the authoritative number | Free — structural |
| Resource exhaustion | CPU, memory, PID, file-size and wall-clock caps; the cgroup is reaped on breach | Free — structural |
| Sandbox escape | gVisor or a microVM; no Docker socket; no wallet mounts; non-root | Ongoing |
| Weight copying between validators | Commit-reveal weights, independently signed score artifacts | Free — structural |
| Champion cloning | Statistical dominance margin a copy cannot clear; earliest-commitment tie-break | Low |

"Free — structural" means the attack is prevented by the shape of the system
rather than by a detector that has to keep winning an arms race.

---

## The reference ladder

A leaderboard showing only miner scores is a marketing artifact. Every round
publishes miner scores alongside:

1. The unmodified repository at the pinned optimization level
2. Pinned `-O2` and `-O3`, plus a PGO reference where the task supports profiling
3. **The human commit** — this defines `S_ref` and therefore the 1.0 point
4. A naive single-shot LLM patcher — the "is the harness doing anything?" control
5. The reference agent shipped in the SDK — the "is the miner beating the freely
   available starting point?" control

That combination is what makes the leaderboard a research instrument rather than a
claim, and it is the most persuasive object the project can put in front of a
technical reader.

---

## Consensus comparability

Every score is bound to:

```
(spec_version, spec_digest, toolchain_digest, corpus_snapshot, hardware_class)
```

Validators refuse to compare scores across differing tuples. A validator running
an outdated specification self-excludes rather than emitting incompatible weights.

Upgrades are scheduled to an activation block, announced publicly, and rehearsed
on testnet. Chain hyperparameters change roughly once per two tempos, so at a
24-hour tempo a configuration mistake costs two days.

---

## Honest limits

**Semantic equivalence is inherently incomplete.** Passing a finite differential
suite is not proof of universal equivalence. Every claim this system emits carries
its scope with it and never claims more. Users needing stronger guarantees should
be pointed at translation validation or formal methods, and told plainly that this
does not provide them.

**Benchmark overfitting is a permanent risk, not a solved problem.** Future-seeded
tasks, timelocked inputs, hidden families, procedural generation and scheduled
corpus refresh raise the cost. They do not eliminate it. The monitoring signal is
public scores rising while hidden-family capture stays flat.

**The mechanism has to earn its existence.** If open competition cannot beat a
strong single-agent baseline on hidden repositories, emissions are paying for
nothing. That is a measurable question, and the reference ladder exists so it can
be answered in public rather than argued about.
