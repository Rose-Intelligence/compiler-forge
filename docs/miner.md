# Miner guide

You are building a **reusable optimization agent**: a container image that, handed
a repository it has never seen and a fixed budget, returns a patch that makes the
code measurably cheaper to run without changing what it does.

You are not submitting patches. You are not submitting a model. You are submitting
a search process, and it will be judged on repositories you cannot see.

---

## Contents

- [How you get paid](#how-you-get-paid)
- [The artifact contract](#the-artifact-contract)
- [What the validator hands you](#what-the-validator-hands-you)
- [What you hand back](#what-you-hand-back)
- [The gates your patch must survive](#the-gates-your-patch-must-survive)
- [Local development](#local-development)
- [Building your agent](#building-your-agent)
- [Submitting](#submitting)
- [Running the miner neuron](#running-the-miner-neuron)
- [What actually wins](#what-actually-wins)
- [Troubleshooting](#troubleshooting)

---

## How you get paid

Your artifact is evaluated on tasks drawn from a corpus you only partly know
about. On each task you earn **capture** — the fraction of a human expert's
speedup you achieved on that task:

```
capture = clamp( (your_speedup_lower_bound − 1) / (expert_speedup − 1),  0,  2.0 )
```

- `0.0` — no credible improvement
- `1.0` — you matched the expert commit
- `2.0` — you comfortably beat it (capped, so no single task can carry you)

Note the **lower bound**. Measurement uncertainty is charged to you, never to the
network. A noisy 1.20x scores worse than a confident 1.15x.

Captures are combined across tasks in log space, so one enormous win cannot
compensate for repeated failures. Four solid results beat one spectacular one
plus three zeroes.

Full mechanism: [incentive_mechanism.md](incentive_mechanism.md).

### Two things worth internalising early

**An honest empty result scores above a rejected patch.** If your agent finds
nothing it can safely change, returning no patch is a legitimate outcome: it
passes every gate, contributes zero capture, and still earns from the floor pool.
Submitting something that fails a correctness gate earns nothing *and* records a
reliability penalty against your artifact.

**Nothing you claim is scored.** The `self_measurement` field in your report is
recorded and never used for payment. It exists so the network can calibrate how
well agents predict themselves — agents that systematically overstate are visible.

---

## The artifact contract

| Item | Requirement |
|------|-------------|
| Format | OCI container image, publicly pullable, pinned by sha256 digest |
| Entrypoint | One command reading `/task/task.json`, writing `/output/patch.diff` and `/output/report.json` |
| Network | **Disabled**, except a loopback inference proxy when the task allows it |
| Size | 8 GiB uncompressed, hard cap |
| User | Non-root. The sandbox forces it; build for it |
| Filesystem | Read-only root. `/output` is writable; `/tmp` is a 2 GiB tmpfs |
| Determinism | Must honour `CF_SEED` so a replay audit can reproduce your run |
| Budget | Wall-clock, tokens, CPU, memory and PIDs are capped and enforced |

**Commit a digest, never a tag.** A tag can be repointed after a round begins,
which would defeat the guarantee the entire competition rests on. The submission
tooling refuses to commit anything it cannot resolve to a digest.

**Why the network is off.** An optimization benchmark with network access is not
measuring optimization ability, it is measuring retrieval — an agent that can
fetch the upstream fix commit for the repository it is being scored on has learned
nothing. Your agent gets the repository, the task contract, and if the task allows
it, a metered proxy to a validator-pinned model. Nothing else.

---

## What the validator hands you

`/task/task.json`:

```json
{
  "spec_version": 1,
  "interface_version": "cf/1",
  "task_id": "sha256:9f2c...",
  "repository": {
    "uri": "mounted:///workspace/repo",
    "revision": "a41e9c8...",
    "license": "MIT"
  },
  "build": {
    "command": "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j8",
    "toolchain_digest": "sha256:1b77...",
    "forbidden_flags": ["-ffast-math", "-fno-strict-aliasing"],
    "second_opt_level": "-O1"
  },
  "tests": {
    "public_command": "./build/test_csvsplit",
    "hidden_contract": "validator-owned",
    "inventory_hash": "sha256:c4a0...",
    "differential_command": "./build/differential"
  },
  "equivalence": {
    "discipline": "structural",
    "float_tolerance_ulp": null,
    "relative_error_budget": null,
    "side_effects": ["stdout", "exit_code"]
  },
  "benchmark": {
    "command": "./build/bench --lines 800 --fields 10 --repeats 3",
    "objective": "balanced",
    "measured_region": "cf_bench_start/cf_bench_stop",
    "max_wall_seconds": 1800
  },
  "resources": {
    "cpu_cores": 8, "ram_gb": 16, "disk_gb": 32,
    "pids_max": 512, "model_token_budget": 150000
  },
  "seed": "0x8c31...",
  "network": "none"
}
```

Read `equivalence.discipline` carefully — it tells you exactly what you are and
are not allowed to change:

| Discipline | What must hold |
|-----------|----------------|
| `byte_equal` | Output bytes identical |
| `round_trip` | Encoding may differ; the decoder must recover the original |
| `float_tolerance` | Numbers may move within a declared ULP or relative-error budget |
| `structural` | The parse tree is fixed; its serialised formatting is not |
| `state_invariant` | Declared invariants hold; ordering compared only where guaranteed |
| `operation_sequence` | Behaviour under generated operations, **including iterator invalidation** |

Environment variables available to you:

```
CF_SEED                 honour this; replay audits depend on it
CF_TASK_ID
CF_INTERFACE_VERSION
CF_TOKEN_BUDGET
CF_INFERENCE_URL        only when the task sets network: "proxy"
```

---

## What you hand back

`/output/patch.diff` — a unified diff against the pinned revision.

`/output/report.json`:

```json
{
  "interface_version": "cf/1",
  "task_id": "sha256:9f2c...",
  "agent_version": "1.4.0",
  "objective": "balanced",
  "changed_files": ["src/parser/lexer.c"],
  "claimed_strategy": ["allocation removal", "loop-invariant hoisting"],
  "candidate_count": 8,
  "selected_candidate": 5,
  "rejected_reasons": {"3": "differential mismatch", "6": "UBSan finding"},
  "self_measurement": {"local_speedup_estimate": 1.21, "method": "callgrind"},
  "budget_used": {"wall_seconds": 1412, "model_tokens": 96500},
  "notes": "Informational only. Validator measurements are authoritative."
}
```

A missing or malformed report is an **interface violation** — a zero for the
task, not a negotiation. `interface_version` and `task_id` must match what you
were given, and `budget_used.model_tokens` must not exceed the budget.

---

## The gates your patch must survive

Every gate is hard. Failing one produces zero for the task, and measurement never
runs. They are ordered cheapest-first, so most candidates die before anything
expensive happens.

```bash
cf-eval gates      # prints this list with current thresholds
```

| # | Gate | Fails when |
|---|------|-----------|
| 1 | `baseline_stable` | The *task* is unstable. Voided for everyone, not a zero for you |
| 2 | `patch_hygiene` | Too many files or lines; edits the build definition or a test file |
| 3 | `patch_applies` | The diff does not apply to the pinned revision |
| 4 | `build` | Does not build with the pinned toolchain, or uses a forbidden flag |
| 5 | `test_inventory` | Any test file changed — verified by hash, not by counting |
| 6 | `api_abi` | Public headers or ABI changed |
| 7 | `public_tests` | The project's own suite fails |
| 8 | `differential` | Baseline and candidate diverge on hidden inputs |
| 9 | `asan` | AddressSanitizer finds a memory error |
| 10 | `ubsan` | UndefinedBehaviorSanitizer finds undefined behaviour |
| 11 | `fuzz` | Coverage-guided fuzzing finds a crash |
| 12 | `second_opt_level` | Behaviour changes when rebuilt at a different `-O` level |

Two of these deserve explanation.

**`differential` catches what `public_tests` cannot.** The hidden inputs are
generated from the round's block seed and sealed with a timelock, so they did not
exist when you froze your artifact. A patch can pass every public test and still
fail here — that is the gate doing its job, not a bug. The integration suite
demonstrates exactly this case with an order-insensitive hash that merges
anagrams: public tests pass, hidden inputs catch it.

**`second_opt_level` catches fake speedups.** Code whose speed depends on the
compiler exploiting undefined behaviour usually changes behaviour when the
optimizer's assumptions change. A candidate that introduces UB is not fast; it is
unsound and merely appears fast under one configuration.

---

## Local development

The SDK runs **the same gate sequence a validator runs, using the same code**.

```bash
cf-eval preflight     # what can this machine measure?
```

Evaluate a patch directly:

```bash
cf-eval patch corpus/string-split --patch my-optimization.diff --seed 0xdead
```

Run your agent end to end:

```bash
cf-eval agent corpus/string-split --entrypoint "python3 my_agent.py" --seed 0xdead
```

Get a `task.json` to develop against by hand:

```bash
cf-eval write-task corpus/string-split --seed 0xdead --output task.json
```

Try several seeds and several profiles. On chain the seed comes from a block hash
you cannot predict, and the workload profile is drawn from it too — including
profiles that are *not* in the published manifest.

### Two honest differences from a real round

1. **Hidden inputs.** Locally you generate inputs from a seed you chose. A
   validator uses sealed inputs you have never seen. Passing locally is weaker
   evidence than passing on chain.
2. **Isolation.** Locally your agent runs as an ordinary subprocess. On a
   validator it has no network, a read-only root filesystem, dropped capabilities
   and hard resource caps. Code that quietly reaches for the network works here
   and fails there.

---

## Building your agent

The reference agent in `compilerforge/miner/reference_agent/` is a working
implementation of the loop that gets rewarded. It exists as a control — the
"is the miner beating the freely available starting point?" rung of the published
leaderboard ladder — not as a competitor.

Read it, then beat it. The loop it demonstrates:

1. **Reproduce the baseline.** Build it, run it, measure it.
2. **Profile.** Rank functions by instruction share. Prioritise against the
   declared objective, not against whatever is easiest to change.
3. **Generate several candidates.** Stopping at the first plausible edit is the
   single most common way to leave capture on the table.
4. **Verify each one locally.** Build, check behaviour, measure. Discard on any
   doubt — the validator's gates are stricter than yours.
5. **Return the best survivor, or nothing.**

### Measure the way the validator measures

This is the highest-value thing you can do. The validator scores you on
**instruction count under Callgrind**, not wall-clock:

```bash
valgrind --tool=callgrind --instr-atstart=no --cache-sim=yes \
         --callgrind-out-file=cg.out ./build/bench <args>
```

A harness that optimizes against wall-clock on a shared machine is optimizing
against noise. Two subtleties the reference agent already handles:

- With `--instr-atstart=no`, Valgrind 3.22 leaves `summary:` at a single zero and
  puts the bracketed cost in `totals:`. Read the wrong line and you will conclude
  your patch did nothing.
- With `--trace-children=yes`, include `%p` in the output filename or the
  processes clobber each other's profiles.

### Dockerfile

`compilerforge/miner/reference_agent/Dockerfile` is a working starting point. The
essentials:

```dockerfile
FROM docker.io/library/debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        clang cmake make valgrind python3 diffutils patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /agent
COPY agent.py /agent/

# The sandbox forces non-root. Build for it rather than being surprised by it.
USER 65534:65534

ENTRYPOINT ["python3", "/agent/agent.py"]
```

Do not bake model weights into the image. The validator mounts the pinned model
and the repository at runtime, and the 8 GiB cap exists partly because a large
image is the natural hiding place for an embedded answer table.

---

## Submitting

```bash
# 1. Check the image against the artifact rules before spending a chain call
cf-miner check --image ghcr.io/you/my-optimizer

# 2. Push it — a digest that no registry can serve is a commitment to nothing
docker push ghcr.io/you/my-optimizer

# 3. Commit the digest on chain
cf-miner submit --netuid <netuid> \
    --image ghcr.io/you/my-optimizer \
    --wallet.name miner --wallet.hotkey default

# 4. Confirm
cf-miner status --netuid <netuid> --wallet.name miner --wallet.hotkey default
```

Your artifact competes from the next round whose task-selecting block postdates
your commitment. Committing during a round does not retroactively enter it.

---

## Running the miner neuron

```bash
python neurons/miner.py \
    --netuid <netuid> \
    --wallet.name miner --wallet.hotkey default \
    --subtensor.network finney \
    --artifact.image ghcr.io/you/my-optimizer \
    --artifact.cells generalist,parsing
```

The neuron does not serve requests — validators pull your artifact. It stays
running so that the commitment is re-asserted after a metagraph change, and so
there is one process to supervise.

Under pm2:

```bash
pm2 start neurons/miner.py --name cf-miner --interpreter python3 -- \
    --netuid <netuid> --wallet.name miner --wallet.hotkey default \
    --artifact.image ghcr.io/you/my-optimizer
```

---

## What actually wins

The competitive variable is the **harness**, not model access. Validators supply
a pinned model through a metered proxy, so nobody wins by renting a better one.
What separates artifacts:

**Search breadth under budget.** The budget is fixed. An agent that evaluates
eight candidates and keeps the best beats one that evaluates two, provided its
verification is cheap enough to afford them.

**Knowing when to stop.** Every candidate you verify costs budget you cannot
spend elsewhere. Early-abort on candidates that cannot beat what you already have.

**Rollback discipline.** The gates are unforgiving and a failed candidate earns
nothing. Verify against the strictest interpretation of the declared equivalence
discipline, not the most convenient one.

**Algorithmic and data-structure change.** `-O3` already does the local work. What
it cannot do is remove a quadratic loop, collapse a redundant conversion, replace
a container that is wrong for the access pattern, or eliminate an allocation in a
hot path. That is where the capture is.

**Generalisation.** Every round reserves held-out families that appear in no
published listing. An agent tuned to the public corpus scores well there and flat
on the hidden tier, and the hidden tier is worth 10% of the score on its own.

---

## Troubleshooting

**"missing or malformed /output/report.json"** — Your agent crashed, timed out, or
wrote invalid JSON. Check `cf-eval agent` output; it prints interface violations
as a checklist rather than stopping at the first one.

**Passes locally, fails `differential` on chain** — Expected, and the system
working. Your patch changes behaviour on inputs your local seed did not generate.
Run several seeds locally, and re-read the declared equivalence discipline.

**`patch_hygiene` fails on a patch you think is fine** — You are almost certainly
touching `CMakeLists.txt`, a `Makefile`, or a file under `tests/`. Changing the
flags you are measured under is measurement manipulation, not optimization.

**`second_opt_level` fails but everything else passes** — Your patch relies on
undefined behaviour. It is not fast; it is unsound and happens to look fast under
one compiler configuration. UBSan may not have caught it, but the rebuild did.

**Task voided** — Not your fault and not a zero. The baseline was unstable or the
two measurement tiers disagreed persistently. The task is dropped for every miner
and the round records no crown change.

**"artifact is N GiB, cap is 8 GiB"** — Trim the image. Multi-stage builds and
dropping apt caches usually suffice.
