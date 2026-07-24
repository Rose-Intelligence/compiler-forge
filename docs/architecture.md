# Architecture

How the system is put together, and why each piece is where it is.

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

**Inputs that target what a rewrite gets wrong.** `token-count`'s generator emits
anagrams (a byte-summing hash merges them), tokens longer than 64 bytes (a
fixed-size stack buffer overflows), and whitespace runs (a hand-rolled tokeniser
emits empty tokens). None of these appear in the public test suite. That is the
point.

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

Two worked packages, both real code that really builds and really measures.

| | `string-split` | `token-count` |
|---|---------------|---------------|
| Family | parsing | data structures |
| Visibility | public | **held out** |
| The inefficiency | `strlen` inside a loop condition, `realloc` one element at a time, malloc/free per line | linear scan over a flat array on every insert and lookup |
| The fix | hoist the scan, allocate once, reuse one buffer | an open-addressing index beside the table |
| The constraint | none | the public struct layout is part of the API and cannot grow a field |
| S_ref | 1.31x – 1.65x by profile | 4.05x – 10.30x by profile |

`token-count` is deliberately a different *shape* of problem, so an agent that
memorises the first learns nothing useful for the second. Its constraint — fixing
a data structure without being able to change the struct that holds it — is the
kind of thing real optimization work runs into constantly.

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
independently with its own weight matrix and bond pools.

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

The integration tests build actual C code and take actual measurements. They are
the tests that caught the bugs unit tests could not:

- a Callgrind cost line read from the wrong place (`summary:` versus `totals:`)
- a cached baseline whose workspace was never built
- a differential harness pointed at the benchmark binary
- an output filename that lets traced child processes clobber each other

One of them measures the same patch twice and requires the instruction counts to
match **exactly**. That is the property the entire consensus design rests on, and
it is worth asserting rather than assuming.
