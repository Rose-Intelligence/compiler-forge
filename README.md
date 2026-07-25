<div align="center">

# CompilerForge

**Verified autonomous software performance engineering**

A Bittensor subnet for agents that make existing software faster, lighter and
cheaper — without changing what it does.

[Miner guide](docs/miner.md) ·
[Validator guide](docs/validator.md) ·
[Architecture](docs/architecture.md) ·
[Incentive mechanism](docs/incentive_mechanism.md) ·
[Dashboard](https://github.com/Rose-Intelligence/cf-dashboard)

</div>

---

## What this network produces

Most code subnets score whether a program is **correct** — by tests, by a judge
model, or by a merge event.

CompilerForge scores how **expensive a correct program is to run**. That makes the
reward signal a physical measurement rather than a model's opinion, and it makes
the output directly monetisable as infrastructure savings.

Miners do not submit patches. They submit a **reusable optimization agent**: a
container image that, handed an unfamiliar repository and a fixed budget,
reproduces the baseline, finds the bottlenecks, generates and prunes candidate
transformations, throws away anything that changes behaviour, and returns a patch
that is measurably cheaper to run.

Validators then execute that agent against repositories the miner has never seen,
and pay only for improvements they reproduced on their own instrumentation.

---

## The one hard problem, and how it is solved

A permissionless validator set does not own identical hardware.

Real benchmark suites on commodity machines vary by around **2.7%** run to run.
A genuine 3% improvement measured by two honest validators will frequently
disagree about its *sign*. Their weight vectors diverge, Yuma clipping punishes
them, and validator trust decays for reasons that have nothing to do with
dishonesty.

So measurement happens in two tiers:

| Tier | Instrument | Varies by | Role |
|------|-----------|-----------|------|
| **A** | Callgrind on a simulated CPU | ~0.000001% | **Decides the score.** Hardware-independent by construction. |
| **B** | Wall-clock on a calibrated bare-metal host | ~2.7% | Reporting, and a sign-agreement gate. Never ranks. |

Tier B's only power is to contradict: if the deterministic tier says a patch is
faster and a calibrated host says it is *confidently* slower, the task is re-run,
and persistent divergence voids it. That catches the interesting adversarial case
— an "optimization" that cuts instruction count while destroying memory-level
parallelism — without requiring every validator to buy the same machine.

> Wall-clock is a legitimate business metric and an illegitimate consensus metric.

This is verified, not asserted. `tests/test_integration.py` measures the same
patch twice and requires the instruction counts to match **exactly**.

---

## How a round works

```
  miner ──commit(image digest)──► chain
                                    │
                          ┌─────────┴─────────┐
                          │  artifact set     │   frozen at a pinned block
                          │  is now frozen    │
                          └─────────┬─────────┘
                                    │
                          a LATER block hash
                                    │
                     ┌──────────────┴──────────────┐
                     │  selects repository,        │   entropy that did not exist
                     │  revision, workload, seed   │   when the artifact was frozen
                     └──────────────┬──────────────┘
                                    ▼
                        agent runs in a sandbox
                     (no network, non-root, capped)
                                    │
                                    ▼
              build │ interface │ differential │ fuzz │ sanitize
                                    │
                                    ▼
                    Tier A measurement  ─────►  score
                                    │
                                    ▼
                  commit-reveal weights ──► Yuma Consensus
```

Because the task-selecting block hash postdates every commitment, "this artifact
was not tuned to this task" stops being a promise and becomes something a third
party can check from public data.

Hidden test material is published in advance as **drand-timelocked ciphertext**.
Before the reveal round nobody can read it — including the validator that sealed
it. Afterwards, anyone can open it and re-run the audit.

---

## The score

Raw speedup is a poor primitive: unbounded, dominated by one lucky repository,
and silent about how much of the available headroom was actually captured. Every
task instead carries a **reference optimization** — the human expert commit, or a
curated equivalent — and artifacts are scored on the fraction of it they achieved:

```
capture = clamp( (S_lcb − 1) / (S_ref − 1),  0,  2.0 )

  0.0   no credible improvement
  1.0   matched the human expert
  2.0   comfortably beat the expert (capped, so one task cannot carry an artifact)
```

`S_lcb` is a **lower** confidence bound, so measurement uncertainty always costs
the miner and never the network — the correct direction for an incentive system
to be wrong in.

Full weighting, dethronement rules and the emission policy: **[incentive
mechanism](docs/incentive_mechanism.md)**.

---

## Quick start

### Requirements

Python 3.11+ and Bittensor SDK 11. The v11 API is a ground-up rewrite — it has no
`bt.Config`, no `bt.logging`, and reads and writes go through
`subtensor.read(...)` and typed intents — so this subnet targets it directly and
will not run against v10.

For validators, a working C toolchain is also required:

```bash
sudo apt-get install -y clang cmake valgrind git
```

`valgrind` is not optional for a validator. It provides the consensus-bearing
measurement, and a validator without it refuses to start rather than emitting
weights nobody else can reproduce.

### Install

```bash
git clone https://github.com/Rose-Intelligence/compiler-forge
cd compiler-forge
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This step is what puts the commands on your `PATH`. Without it there is no
`cf-eval`, and every example below fails with `command not found`:

| Command | For |
|---|---|
| `cf-eval` | optimizing and verifying — the one you want first |
| `cf-miner` | packaging an agent as an artifact |
| `cf-validator` | running a validator |
| `cf-corpus` | building and measuring corpus packages |

Check it took:

```bash
cf-eval --help
```

### Check your machine

```bash
cf-eval preflight
```

### Run the tests

```bash
pytest                 # fast suite, ~1s
pytest -m slow         # end-to-end against the real toolchain, ~70s
```

### Try an optimization

```bash
# What must a patch survive?
cf-eval gates

# Evaluate the reference patch. Capture should come out at 1.000 by definition.
cf-eval patch corpus/string-split --patch corpus/string-split/reference.patch

# Run an agent end to end.
cf-eval agent corpus/string-split \
    --entrypoint "python3 compilerforge/miner/reference_agent/agent.py"
```

---

## Using it on your own code

You do not need to run a miner, a validator, or anything on chain to use the
measurement pipeline. Two commands take a source tree to a verified,
reproducible speedup.

```bash
# 1. Turn your project into something measurable.
cf-eval onboard ./my-project --out ./pkg --bench-args "--lines 800"

# 2. Check a change is correct and find out what it saved.
cf-eval verify ./pkg --patch my-change.diff
```

`onboard` reads the build system, the test target and the benchmark out of your
own build definition, works out which directories a candidate may rewrite, and
tells you what it could not determine rather than guessing. A project with no
build system at all still works if a source file defines `main()` — a minimal
build is generated for it.

`verify` runs the same gate sequence and the same measurement a validator runs,
and stops before scoring. Scoring needs a measured expert patch to normalise
against, and your repository has none — but correctness and speed never depended
on that, so this reports both.

Two things decide whether the number is worth much:

**Instrument the benchmark.** Bracket the hot region with
`CALLGRIND_START_INSTRUMENTATION` / `CALLGRIND_STOP_INSTRUMENTATION` from
`<valgrind/callgrind.h>`. Without them the whole process is measured, startup
included — and for a small program that is nearly all of it. A real submission
measured 1,975,095 instructions of which 1,961,536 were iostream initialisation:
the program's own work was 0.69% of the count, and no optimization of it could
have shown up.

**Keep the benchmark out of the patch scope.** If the code being measured and
the code being changed are the same file, a candidate that computes less and
prints the same bytes is indistinguishable from one that computes the same thing
faster. `onboard` warns when it detects this.

---

## Repository layout

```
neurons/
  miner.py                  miner entrypoint
  validator.py              validator entrypoint

compilerforge/
  spec.py                   versioned consensus constants — changing these is a fork
  protocol/                 wire contracts: task, report, score artifact, commitment
  corpus/                   task packages, manifests, equivalence comparators
  sandbox/                  untrusted artifact execution, metered inference proxy
  evaluation/               the gate sequence and the two measurement tiers
  scoring/                  capture, aggregation, dethronement, emissions
  chain/                    commitments, sealed task material, public audit bundles
  base/                     base neuron classes
  validator/                the round loop
  miner/                    miner neuron and the reference agent
  sdk/                      the local evaluator miners develop against

corpus/
  string-split/             worked example: a parser with real inefficiency
  token-count/              held-out package used to measure generalisation

docs/                       guides and runbooks
tests/                      183 fast tests, 15 marked slow
```

The performance dashboard is a **separate project** —
[Rose-Intelligence/cf-dashboard](https://github.com/Rose-Intelligence/cf-dashboard).
It renders published round bundles: the reference ladder, the champion race, crown
status, the miner leaderboard and validator agreement.

It lives apart on purpose. It holds no keys, has no privileged read, and cannot
influence a score — a claim worth being able to check by reading it, which is only
easy while it stays a dependency-free static page rather than a directory inside a
repository that signs weights. Clone the two side by side and its fixture generator
finds this checkout with no arguments.

---

## Design commitments

These are constraints the implementation actually enforces, not aspirations.

**Fail closed.** A missing task source, an unreadable artifact, a toolchain
mismatch or an incompatible consensus version produces *no score at all*, never a
guessed one. A validator that cannot measure something correctly is worth more to
the network silent than approximately right.

**Score locally, sign what you measured.** No operator-computed weight vector is
ever relayed. Every number a validator submits comes from a measurement that
validator made itself — which is the entire reason independent validation is
worth paying for.

**No network during evaluation.** An optimization benchmark with network access
is not measuring optimization ability; it is measuring retrieval. Agents run with
no external network, a read-only root filesystem, dropped capabilities, non-root,
and hard CPU, memory, PID and file-size caps.

**Pay for verified work, do not burn.** Withholding miner emission reduces the
subnet's own TAO inflow proportionally. When no artifact clears the improvement
threshold, emission flows to artifacts that passed every correctness gate and
returned an honest null result. Burn is retained only as an audited safety valve.

**An honest empty result beats a rejected patch.** An agent that finds nothing
safe to change and says so scores above one that submits something broken, and it
still earns from the floor pool.

**Evidence, not proof.** Passing a finite differential suite is not universal
equivalence. Every equivalence claim this software emits carries its scope
alongside it, and never claims more.

**No silent failures.** A wrong number that looks right is worse than no number.
A task that cannot be measured is voided rather than scored as zero for everyone;
an evaluation this validator could not perform emits no score rather than
punishing the miner for it; a rejected extrinsic raises rather than being reported
as success. The full contract is in [failure_handling.md](docs/failure_handling.md),
and `tests/test_no_silent_failures.py` pins it.

---

## What this does not claim

Autonomous performance optimization is not a new idea, and this project does not
pretend otherwise. Several companies sell measured code optimization today, and
compiler-ML systems are mature production technology.

The defensible asset here is not the agent. It is the **evaluation apparatus** —
hidden cross-repository tasks, deterministic measurement, reproducible audit
bundles — plus the compounding registry of verified techniques it produces.

The network is justified only if open competition produces stronger optimizers
faster than a well-funded internal team would, and only if validator consensus
stays credible under adversarial pressure. Decentralisation is not itself the
argument.

---

## Contributing

See [contrib/CONTRIBUTING.md](contrib/CONTRIBUTING.md) and
[contrib/STYLE.md](contrib/STYLE.md). New task packages are the single most
valuable contribution; see the [architecture guide](docs/architecture.md#adding-a-task-package).

## License

MIT — see [LICENSE](LICENSE).
