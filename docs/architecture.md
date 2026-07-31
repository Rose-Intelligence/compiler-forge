# Architecture

How the system is put together, why each piece is where it is, how a measurement
becomes a weight, and what happens when something goes wrong.

- [How it is put together](#two-planes)
- [Incentive mechanism](#incentive-mechanism) — how a measurement becomes a weight
- [Failure handling](#failure-handling) — what happens when it cannot

---

## Two planes

```
  PUBLIC RESEARCH PLANE                    PRIVATE EXECUTION PLANE
  ─────────────────────                    ───────────────────────
  miner ──commit(digest)──► chain          customer repository
    │                        │             (never leaves the perimeter)
    │                        ▼                      │
    │              block hash + timelock            ▼
    │                   task selection      champion image (pinned digest)
    ▼                        │                      │
  OCI image ──pull──► validator sandbox             ▼
                             │              verified patch + evidence
                    build │ diff │ fuzz             │
                          │ measure                 ▼
                             │                 human reviewer
                             ▼
                    signed score artifact
                             │
                   commit-reveal weights ──► Yuma Consensus ──► emissions
```

The only thing that ever crosses the boundary is the champion image digest. Never
task data, never private source.

---

## Package layout

```
neurons/
  miner.py            entrypoint — commits an artifact digest
  validator.py        entrypoint — runs evaluation rounds

compilerforge/
  spec.py             every constant two validators must agree on
  protocol/           wire contracts
    task.py             what the validator hands the agent
    report.py           what the agent hands back (never scored)
    score.py            the signed score artifact
    commitment.py       the on-chain payload
  corpus/
    package.py          task package format, manifests, content hashing
    equivalence.py      per-family comparators
    cli.py              cf-corpus
  sandbox/
    isolation.py        the security boundary, as enforced code
    runner.py           digest-pinned artifact execution
    proxy.py            metered inference proxy
  evaluation/
    selection.py        block hash -> task set (pure function)
    baseline.py         baseline reproduction and caching
    build.py            patch application, build, interface gates
    differential.py     hidden-input equivalence testing
    safety.py           fuzzing, sanitizers, second-opt-level check
    measurement.py      the two tiers and the sign-agreement gate
    statistics.py       bootstrap confidence bounds, robust statistics
    pipeline.py         the gate sequence, in order
  scoring/
    capture.py          the per-task primitive
    aggregate.py        cross-task and cross-validator combination
    dethronement.py     champion / challenger
    emissions.py        weight vectors, floor-and-decay
  chain/
    commitments.py      reading frozen artifacts
    sealed.py           drand timelock for hidden material
    hyperparameters.py  the configuration plan, as checkable data
    audit.py            public round bundles
  base/                 base neuron classes
  validator/            round loop
  miner/                miner neuron, submission CLI, reference agent
  sdk/                  the local evaluator miners develop against
```

---

## Dependency direction

```
protocol  ──►  corpus  ──►  evaluation  ──►  scoring  ──►  validator
                              ▲
   sandbox ────────────────────┘
                                              chain ──────►  validator
```

`protocol` depends on nothing but `spec`. Nothing depends on `validator`. The
`sdk` reaches into `evaluation` and reuses the same code paths a validator runs,
which is what makes local results meaningful.

---

## Determinism boundaries

Three things must be byte-identical across independent validators. Everything else
is allowed to vary.

**Task derivation.** `evaluation/selection.py` is a pure function of
`(block hash, corpus snapshot, consensus digest)`. It uses an explicit
counter-based shuffle rather than `random.Random`, because the standard library's
generator stream is an implementation detail that could change between Python
versions — and a validator upgrading Python must not silently start evaluating a
different task set.

**Deterministic measurement.** Callgrind runs on a simulated CPU. Repeated runs
must be identical; variation is treated as a non-deterministic benchmark and the
task is rejected rather than averaged.

**Bootstrap resampling.** `evaluation/statistics.py` seeds its generator from a
hash of the data itself, so two validators resampling the same measurements reach
the same confidence bound. The bound feeds the weight vector directly, so this
cannot be left to chance.

---

## The gate sequence

Ordered cheapest-first. Failure at any gate produces zero for the task and
measurement never runs.

```
baseline_stable      the task itself is measurable      (void for everyone if not)
patch_hygiene        size, scope, no build/test edits
patch_applies        applies to the pinned revision
build                builds with the pinned toolchain
test_inventory       every test file unchanged, by hash
api_abi              public headers and ABI conform
public_tests         the project's own suite passes
differential         agrees with the baseline on hidden inputs
asan                 no memory error
ubsan                no undefined behaviour
fuzz                 no crash under coverage-guided fuzzing
second_opt_level     behaviour unchanged at a different -O level
─────────────────────────────────────────────────────────────
tier_a               deterministic cost      -> the score
tier_b               wall-clock              -> reporting + sign gate
```

Two gates catch things nothing else can.

**`differential`** catches what the public test suite misses. Hidden inputs are
generated from the round seed and sealed with a timelock. The integration suite
demonstrates a patch that passes every public test and fails here: an
order-insensitive hash that merges anagrams, which the public tests never exercise
and the input generator emits deliberately.

**`second_opt_level`** catches speedups that only exist because the compiler was
allowed to assume something the code violates. If observable behaviour changes
with the optimization level, the patch is unsound regardless of what the benchmark
says.

---

## Adding a task package

A package is a directory:

```
corpus/my-package/
  package.yaml          the declarative contract
  repo/                 the pinned source tree
    CMakeLists.txt
    include/            public API — the api_abi gate compares these
    src/
    tests/              validator-owned; verified by inventory hash
      differential.c    reads one input on stdin, prints every observable
    bench/
      bench.c           brackets the measured region
  reference.patch       the expert optimization defining S_ref
  tools/
    generate_cases.py   deterministic hidden-input generator
```

### What makes a good package

**A real inefficiency, not a bug.** The baseline must be correct and pass every
test. It should simply do more work than the job requires: a redundant scan, a
container wrong for the access pattern, an allocation in a hot path. If `-O3`
already fixes it, it does not belong here.

**A bracketed measured region.** `bench.c` must call the Callgrind client
requests around the work being measured, so startup and input generation are
excluded. Without the bracket, a small kernel's real cost is swamped by process
setup, and an "optimization" that moves work outside the region measures as free.

**A differential harness separate from the benchmark.** The benchmark is
argument-driven and exists to be expensive. The differential harness is
input-driven and exists to be comprehensive. Using one for the other silently
compares the wrong thing.

**A deterministic input generator.** A pure function of the seed. The shipped
generators use an explicit counter-based stream rather than `random`, for the same
reason task selection does.

**Inputs that target what a rewrite gets wrong.** A package's generator emits the
cases a naive rewrite mishandles — collisions a weak hash merges, boundary-length
tokens that overflow a fixed buffer, degenerate runs a hand-rolled loop gets wrong
— none of which appear in the public test suite. That is the point: correctness is
checked on inputs the agent was never shown.

**A workload profile axis that actually matters.** Vary the parameter that decides
how badly the inefficiency hurts, and mark at least one profile unpublished so it
cannot be tuned against.

### Building it

```bash
# 1. Verify the baseline builds and the tests pass
cd corpus/my-package/repo && cmake -S . -B build && cmake --build build && ./build/test_mine

# 2. Confirm the reference patch preserves behaviour and is faster
cf-eval patch corpus/my-package --patch corpus/my-package/reference.patch

# 3. Measure and freeze S_ref for every profile
cf-corpus measure-reference corpus/my-package

# 4. Validate the package against the corpus rules
cf-corpus validate ./corpus

# 5. Seal the hidden inputs
cf-corpus seal corpus/my-package --hours 36
```

Step 3 is not optional. A package with no measured reference cannot be scored:
capture has nothing to normalise against, and the validator voids the task rather
than inventing a denominator.

---

## The corpus in this repository

Two worked public packages, both real code that really builds and really measures.

| | `string-split` | `sorted-index` |
|---|---------------|---------------|
| Family | parsing | data structures |
| The inefficiency | `strlen` inside a loop condition, `realloc` one element at a time, malloc/free per line | every lookup walks a sorted array, ignoring the order it already guarantees |
| The fix | hoist the scan, allocate once, reuse one buffer | binary-search the keys it already keeps sorted |
| The constraint | none | none — the win is noticing an invariant, not restructuring code |
| S_ref | 1.31x – 1.65x by profile | 4.77x – 25.34x by profile |

`sorted-index` is deliberately a different *shape* of problem from `string-split`,
so an agent that memorises the first learns nothing useful for the second: one is
a redundant scan over text, the other an algorithmic property of sorted data a
rewrite has to *notice*. The held-out families (kept in the private corpus) test
exactly this transfer — that technique carries to a problem the agent never saw.

---

## Chain integration

**Commitments.** A miner writes one small payload: image repository, sha256
digest, interface version. Validators read them at a pinned historical block, so
two validators freeze the same set even if a miner commits mid-round.

**Block entropy.** The task-selecting hash is drawn from a block that postdates
every commitment. The validator refuses to ask for a block that has not been
produced.

**Timelock.** Hidden material is sealed against a future drand round. Before it,
nobody can decrypt — including the sealer. Afterwards, anyone can open it and
re-run the audit. This is the difference between a benchmark you believe and one
you can check.

**Two mechanisms.** `set_weights(..., mechid=N)` targets the generalist
championship and the specialist/bounty lane separately. Each runs Yuma Consensus
independently with its own weight matrix and bond pools. Before the owner raises
the mechanism count, the second lane does not exist on chain, and the validator
folds its vector into mechanism 0 so specialist work still earns.

**Commit-reveal.** A chain hyperparameter, not a call flag. With it enabled the
SDK performs the commit and the later reveal itself; nothing here reimplements it.

---

## Testing

```bash
pytest              # 108 unit tests, ~1s
pytest -m slow      # 8 integration tests against the real toolchain, ~70s
```

The unit tests cover the mechanism: capture normalisation, log-space aggregation,
dethronement resistance to cloning, floor-and-decay emissions, selection
determinism, every equivalence comparator, the Callgrind parser, and the security
boundary.

The integration tests build actual C code and take actual measurements. They
cover what unit tests cannot: reading the Callgrind cost line from the right
place (`totals:` versus `summary:`), rebuilding a cached baseline's workspace,
pointing a differential harness at the correct binary, and output filenames that
keep traced child processes from clobbering each other.

One of them measures the same patch twice and requires the instruction counts to
match **exactly** — the property the entire consensus design rests on.

---

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
shape than another. In the shipped corpus, `sorted-index`'s reference is 4.77x on a
small index and 25.34x on a large one — the same patch, the same code.

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
the round — not only the tasks an artifact attempted. A task it did not clear (no
patch produced, or a patch that failed a gate) enters as capture zero; a task
voided for everyone is excluded. The generalist crown is therefore a claim about
breadth, and a single favourable task cannot reach — and freeze — the ceiling.

This is why one enormous win cannot mask repeated failures. Four tasks at 0.5
capture beat one at 2.0 and three at zero — verified in the test suite.

Generalist and per-cell specialist scores are published separately, so an artifact
cannot take the generalist crown by dominating a single library family.

---

## Two-stage validation

Generalist weight is assigned in two stages, so the held-out set — not a single
score component — is what decides rank.

**Stage 1, the public screen.** A miner is ranked only if it clears **more than
60%** of the public tasks, where a public task *passes* when it clears every
correctness gate **and** captures at least **0.25** of the reference speedup.
Valid-but-trivial output does not clear the screen; a miner below the bar earns
nothing that round.

**Stage 2, the held-out ranking.** The survivors are ranked by their capture on
the held-out (generalisation) suite. The crown goes to the best survivor and the
rest share the pool in proportion to held-out capture, so an agent that clears the
public screen but generalises poorly ranks last.

The screen is a proportion, so it self-scales as the corpus grows, and it makes
overfitting worthless: clearing the public set is the price of entry, and the
private set nobody can pre-tune to is what pays. Both stages read the same
block-hash-derived round, so commit-before-entropy still holds. If no miner clears
the screen, the round falls back to the honest-null floor rather than withholding
emission (a silent burn). The thresholds are versioned consensus constants
(`spec.ScreeningSpec`); changing them is a `spec_version` bump.

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

Every round publishes miner scores alongside a set of controls:

1. The unmodified repository at the pinned optimization level
2. Pinned `-O2` and `-O3`, plus a PGO reference where the task supports profiling
3. **The human commit** — this defines `S_ref` and therefore the 1.0 point
4. A naive single-shot LLM patcher — the "is the harness doing anything?" control
5. The reference agent shipped in the SDK — the "is the miner beating the freely
   available starting point?" control

Publishing these controls alongside the miner scores lets a reader judge whether
open competition is adding anything over the freely available baselines, rather
than reading a bare ranking.

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

---

# Failure handling

The rule this codebase is built around:

> A wrong number that looks right is worse than no number at all.

A validator that cannot measure something correctly is worth more to the network
silent than approximately right. This page is the contract that follows from
that, and where each part of it is enforced.

---

## Who gets blamed when something goes wrong

Most of the damage a system like this can do comes from attributing a failure to
the wrong party. There are four distinct outcomes, and they must never be
confused with one another.

| Outcome | Meaning | Effect on the miner | Effect on the round |
|---------|---------|---------------------|---------------------|
| **Gate failure** | The candidate genuinely failed a correctness gate | Zero for the task, reliability penalty | Round continues |
| **Honest null** | Every gate passed, no improvement found | Zero capture, **no penalty**, earns from the floor pool | Round continues |
| **Task voided** | The *task* is unmeasurable — unstable baseline, no hidden inputs, persistent tier divergence | Nothing. The task is dropped for everyone | No crown change |
| **Evaluation error** | *This validator* could not evaluate the pair | Nothing. No score is emitted for the pair | Round continues, error recorded |

The last two are the ones easy to get wrong:

- A task package whose input generator fails must not yield an empty case list
  that fails the differential gate for **every** miner, turning a corpus problem
  into a set of undeserved zeroes. It voids the task instead.
  → `RoundRunner._prepare_cases` returns `(cases, unpreparable)`.

- A crash inside the evaluator must not produce a zero-score artifact that
  punishes the miner for a fault on the validator's side. It emits **no score**
  for that pair, marked `voided=True` so aggregation excludes it, and records the
  error so a validator crashing on everything is visible rather than looking like
  a round where nobody improved anything.
  → `RoundResult.evaluation_errors`.

Both are pinned by `tests/test_no_silent_failures.py`.

---

## A miner must not be able to void a task

Voiding removes a task from every miner's score **and** prevents a crown change
that round. Conflating "this patch cannot be measured" with "this task is broken"
would therefore hand any miner both a denial of service and a way for an
incumbent champion to defend itself indefinitely.

So the two are separate exceptions:

| Failure | Raises | Effect |
|---|---|---|
| The **baseline** cannot be measured | `TaskVoided` | Task dropped for everyone |
| The **candidate** cannot be measured | `CandidateUnmeasurable` | Zero for that artifact only |

The candidate's own determinism is checked before the two sides are compared, so
a non-deterministic patched build is attributed to the patch rather than to the
task.

## Chain operations

Every chain read and write goes through `compilerforge/chain/access.py`, so there
is exactly one place where "the chain said no" becomes an exception.

**A read that fails raises.** No read returns an empty list or a zero on failure.
A validator that cannot read the metagraph produces no weights.

**A rejected extrinsic raises.** The SDK reports failure in the returned result
rather than by raising, so a caller who ignored the return value would treat a
rejected weight submission as success. `_succeeded()` defaults to `False` for any
result shape it does not recognise — guessing "probably fine" is exactly how
weights get reported as set when they were not.

**A future block is refused.** `block_hash()` checks the head first. Asking for a
block that has not been produced is the specific mistake that would break the
freeze-before-entropy ordering the whole competition rests on.

**Both dataclass and mapping shapes are handled.** The SDK returns dataclasses
for some reads and plain dicts for others; assuming one shape would silently read
`None` from the other.

---

## Configuration

`NeuronConfig` raises on an unknown key rather than returning `None`.

This is not pedantry. A validator that read `config.neuron.tasks` as `None`
because the real name is `public_tasks` would evaluate zero tasks and report a
perfectly healthy round.

`--netuid` has no usable default: a neuron pointed at the wrong subnet reads the
wrong metagraph and sets weights nobody asked for, so `0` is rejected at startup.
Configuration is parsed directly, and `test_every_declared_argument_survives_parsing`
pins that every declared argument survives parsing.

---

## Durable state

**A corrupt validator state file is fatal.** The champion registry and reliability
history are consensus-relevant. Starting "fresh" would hand the crown to whoever
scores highest next round, discarding the defender advantage that makes cloning
unprofitable — and it would do so invisibly. The neuron refuses to start and tells
the operator to restore the file or delete it deliberately.

**A corrupt baseline cache is rebuilt.** The cache is derived data, so this is
recoverable. It is still logged and the bad entry removed, because a cache that
keeps corrupting entries is a disk problem someone needs to see.

**A corrupt audit index is rebuilt from the round directories**, which are the
real record. The damaged file is preserved as `index.json.corrupt` rather than
overwritten, and the index is written atomically so a crash mid-write cannot
produce the problem it is recovering from.

---

## Measurement

**Zero instructions is an error, not a measurement.** A Callgrind profile
reporting no work would make every candidate look infinitely faster than the
baseline. `parse_callgrind_output` raises.

**A truncated cost line is never read as a measurement.** Valgrind 3.22 writes
`summary: 0` when instrumentation is gated; reading that as the result would
report every patch as doing nothing.

**Non-determinism is rejected, not averaged.** Repeated runs on a simulated CPU
must be identical. Variation means the benchmark read the clock, the PID or the
entropy pool, and averaging it would put irreproducible numbers into the weight
vector.

**Missing evidence contributes nothing, and is not renormalised.** A validator
without a calibrated wall-clock host reports a *smaller* score, not an inflated
one built from the components it does have. Where a measurement tool is missing
entirely — `/usr/bin/time` for peak memory — it is logged once with what is being
forfeited, rather than quietly scoring every candidate as having no memory
improvement.

---

## What miners are told

A miner who is told only "malformed report" cannot fix anything. `ArtifactRun`
carries `report_error` so the interface check can say *why* — the parse error, or
that no file was written at all.

The same applies to gates: every `GateResult` carries a `detail` explaining what
failed, and those details are published in the round's audit bundle.

---

## Deliberately loud, deliberately quiet

Not everything should raise. These are logged and continue, on purpose:

- **A malformed on-chain commitment** — the miner's to fix, expected during an
  interface transition. Debug level; the count that survived is logged.
- **One artifact failing to run** — one bad artifact must not end a round for
  everyone else. Logged with a traceback.
- **A failed weight heartbeat** — the heartbeat exists to keep the validator
  inside the activity cutoff, so it must survive a failed beat. Logged at error
  level, because persistent failure means drifting out of consensus.
- **A metagraph resync failure** — keeps the previous metagraph rather than
  turning a network blip into a crash loop. Logged at error level, because
  persistent failure means operating on stale data.

The distinction throughout: **recoverable and visible** is fine, **recoverable and
invisible** is not.
