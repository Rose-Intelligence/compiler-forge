# Validator guide

A validator executes miner artifacts, verifies the patches they produce, measures
what those patches cost, and submits weights derived entirely from its own
measurements.

Two rules govern everything below.

**Fail closed.** A missing task source, an unreadable artifact, a toolchain
mismatch or an incompatible consensus version produces *no score at all* — never
a guessed one. A validator that cannot measure something correctly is worth more
to the network silent than approximately right.

**Score locally, sign what you measured.** No operator-computed weight vector is
ever relayed. Every number submitted comes from a measurement this host made
itself. That is the entire reason independent validation is worth paying for.

---

## Contents

- [Hardware](#hardware)
- [Installation](#installation)
- [Preflight](#preflight)
- [Running](#running)
- [What a round does](#what-a-round-does)
- [The two measurement tiers](#the-two-measurement-tiers)
- [The calibrated wall-clock host](#the-calibrated-wall-clock-host)
- [Cost control](#cost-control)
- [Security](#security)
- [Audit bundles](#audit-bundles)
- [Consensus upgrades](#consensus-upgrades)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

---

## Hardware

The verification fleet can be cheap, elastic and heterogeneous, because the
consensus-bearing measurement runs on a *simulated* CPU and is hardware
independent by construction.

| | Minimum | Recommended |
|---|---------|-------------|
| Cores | 8 | 32 |
| RAM | 32 GB | 64 GB |
| Disk | 500 GB SSD | 2 TB SSD |
| GPU | none | none |

Disk is dominated by the baseline cache. Baseline builds are immutable per
`(revision, toolchain_digest)` and are built once, ever — the cache grows with the
corpus, not with the number of rounds.

The **calibrated wall-clock host** is separate, optional, and must be dedicated
bare metal. See [below](#the-calibrated-wall-clock-host). A validator without one
still carries a full consensus weight.

Full specification: [`min_compute.yml`](../min_compute.yml).

---

## Installation

```bash
sudo apt-get update
sudo apt-get install -y clang cmake valgrind git python3-venv /usr/bin/time
```

`valgrind` is mandatory. It provides the consensus-bearing measurement, and the
validator refuses to start without it rather than emitting weights nobody else can
reproduce.

A hardened container runtime is also required, because this machine holds a hotkey
and runs anonymous code:

```bash
# gVisor — a user-space kernel between the artifact and yours
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list > /dev/null
sudo apt-get update && sudo apt-get install -y runsc
sudo runsc install && sudo systemctl restart docker
```

Kata Containers or Firecracker are equally acceptable. Plain `runc` is refused for
authoritative phases unless `--sandbox.allow_unhardened_runtime` is passed, which
is for local development only.

Then:

```bash
git clone https://github.com/Rose-Intelligence/compiler-forge
cd compiler-forge
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

---

## Preflight

```bash
cf-validator preflight --netuid <netuid>
```

Reports everything that would stop this validator in one pass, before a wallet
is loaded or a chain connection is open — host toolchain, sandbox runtime,
corpus, and whether the subnet itself is configured to accept your weights.

It separates two statuses deliberately:

| Status | Meaning |
|--------|---------|
| `BLOCKING` | The validator will refuse to start, or would produce scores nobody else can reproduce. Exit code 1. |
| `degraded` | It will participate fully in consensus and contribute less evidence than a fully equipped host — an uncalibrated wall-clock host, or missing `/usr/bin/time`. Exit code 0. |

Those must not be confused. Treating a degraded host as blocking keeps honest
validators off the subnet; treating a blocking host as degraded puts
incomparable weights on chain.

Skip the chain half while working offline:

```bash
cf-validator preflight --netuid <netuid> --no-chain
```

Compare live subnet hyperparameters against the plan:

```bash
cf-validator hyperparameters --netuid <netuid>
```

`cf-eval preflight` remains the miner-side equivalent, and
`cf-corpus validate ./corpus` checks the corpus on its own.

A package with no measured reference speedup cannot be scored — capture has
nothing to normalise against — and a corpus with no held-out family cannot
produce a round at all.

---

## Running

```bash
python neurons/validator.py \
    --netuid <netuid> \
    --wallet.name validator --wallet.hotkey default \
    --subtensor.network finney \
    --corpus.dir ./corpus \
    --corpus.snapshot cf-corpus-2026.08 \
    --audit.dir ./audit
```

With the wall-clock tier, on a properly calibrated host:

```bash
    --measurement.tier_b \
    --measurement.tier_b_affinity 4-7
```

Under pm2:

```bash
pm2 start neurons/validator.py --name cf-validator --interpreter python3 -- \
    --netuid <netuid> --wallet.name validator --wallet.hotkey default \
    --corpus.dir ./corpus --corpus.snapshot cf-corpus-2026.08
```

Key arguments:

| Argument | Default | Notes |
|----------|---------|-------|
| `--neuron.epoch_length` | 7200 | Blocks per round (~24h). A full round cannot complete in a default 72-minute tempo |
| `--neuron.public_tasks` | 25 | Public corpus tasks per round |
| `--neuron.hidden_tasks` | 3 | Held-out generalisation tasks. At least one is required |
| `--neuron.freeze_lead_blocks` | 300 | Blocks between freezing artifacts and drawing the selecting hash |
| `--neuron.heartbeat_blocks` | 900 | Weight heartbeat interval. **Not optional** — see below |
| `--measurement.tier_b` | off | Enable wall-clock. Requires a calibrated host |
| `--measurement.fuzz_seconds` | 300 | Fuzzing budget per candidate |

### The heartbeat is mandatory

A validator whose last weight update predates the activity cutoff is excluded
from consensus for that epoch. With a round spanning a full day, that will happen
unless something re-asserts the last commit every few hours. The neuron runs a
heartbeat thread automatically; do not disable it.

---

## What a round does

```
1. Freeze     read every artifact commitment at a pinned historical block
2. Wait       for a LATER block
3. Derive     that block's hash selects repository, revision, workload, seed
4. Produce    run the agents assigned to this validator; publish their patches
5. Verify     independently evaluate EVERY published patch
6. Aggregate  combine into per-artifact standings
7. Crown      apply the dethronement rules
8. Weight     submit one vector per mechanism
9. Publish    write the audit bundle
```

Steps 1–3 are the security argument and **must not be reordered**. Because step 3
draws entropy that did not exist at step 1, "this artifact was not tuned to this
task" becomes checkable from public data rather than a claim anyone has to trust.

### Production once, verification everywhere

If every validator ran every agent, a round would consume roughly 1,000 agent
runs × ~150,000 tokens *per validator*, daily. That is the largest cost in the
system and it buys almost nothing — the expensive part, generating the patch, is
not the part consensus needs to be independent about.

So agent execution happens **once** per `(artifact, task)`, assigned by a seeded
rotation every validator derives identically from the block hash. The resulting
patch and its digest are published. Every validator then independently applies,
builds, differentially tests, fuzzes, sanitizes and measures *that patch*.

Consensus forms over the measurement, which is exactly where independence matters
and where cost is low.

A sample of pairs is re-executed each round by a different producer under the same
seed. A producer whose published patch cannot be reproduced loses eligibility and
its stake weight is challenged.

---

## The two measurement tiers

| Tier | Instrument | Varies by | Role |
|------|-----------|-----------|------|
| **A** | Callgrind, simulated CPU | ~0.000001% | **Decides the score** |
| **B** | Wall-clock, calibrated host | ~2.7% | Reporting + sign-agreement gate |

Tier A is what enters the weight vector. It runs on a virtual CPU, so two
validators on entirely different hardware produce the same number — which is the
property that makes Yuma consensus possible for a physical measurement.

Tier B never ranks. Its only power is to contradict: if Tier A says a patch is
faster and a calibrated host says it is *confidently* slower, the task is re-run,
and persistent divergence voids it. Ordinary wall-clock noise is not enough — the
confidence interval must exclude zero — otherwise 2.7% variation would void
healthy rounds.

Tier A also acts as a determinism detector. Repeated runs on a simulated CPU
should be identical; variation means the benchmark read the clock, the PID or the
entropy pool, and the task is rejected rather than averaged.

---

## The calibrated wall-clock host

Optional. But if you enable it, it must be genuinely calibrated — a wall-clock
number from a shared or virtualised machine is not a weaker measurement, it is a
measurement of something else.

```bash
# Pin the frequency governor
sudo cpupower frequency-set -g performance

# Disable turbo
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo

# Isolate cores (kernel command line, then reboot)
#   isolcpus=4-7 nohz_full=4-7 rcu_nocbs=4-7

sudo apt-get install -y numactl
```

The validator probes these and refuses to run Tier B if they are unmet. It also
monitors thermals and **rejects** a measurement window that drifts rather than
correcting it.

---

## Cost control

A naive implementation re-runs everything for everyone and is economically
absurd. Four mechanisms keep a round affordable, and all four are load-bearing.

**Staged gates.** Cheap correctness gates run first; expensive measurement only
for survivors. Roughly a 1,000 → 400 → 100 funnel over a full round.

**Aggressive caching.** Baseline builds, dependency trees and reference
measurements are immutable per `(revision, toolchain_digest)` and are computed
once, ever.

**Tier B for the top N only.** The calibrated host is the scarcest resource in the
system. Candidates are ordered by Tier A and only the leaders reach it.

**Bounded budgets.** Model calls, candidate counts and wall-clock are capped per
task and enforced by the runner, not requested politely.

Indicative cost for 25 tasks × 40 artifacts:

| Stage | Population | Round total |
|-------|-----------|-------------|
| Baseline | 25 tasks | ~0 (cached after first run) |
| Build + interface + differential | 1,000 pairs | ~167 machine-hours |
| Fuzz + ASan + UBSan | ~400 survivors | ~133 machine-hours |
| Tier A | ~400 survivors | ~20 machine-hours |
| Tier B | top ~100 | ~8 host-hours |

---

## Security

This machine holds a hotkey and runs anonymous, arbitrary code. The boundary is
enforced in code, not documented and hoped for.

**Never reachable by an artifact:** wallet files, the Docker socket, hidden task
material, the network, or any host resource outside its contract. Mount paths are
checked against a deny-list rather than trusted to review; `assert_mounts_safe`
raises on `/var/run/docker.sock` and `~/.bittensor` and anything beneath them.

**Every authoritative run:** no external network, read-only root filesystem, all
capabilities dropped, `no-new-privileges`, non-root, private IPC and cgroup
namespaces, and hard caps on CPU, memory, PIDs and file size. On breach the whole
cgroup is reaped.

**Preparation is separate from execution.** Package mirrors are available while
preparing an environment and refused during the authoritative phases.

**Profiler output is validator-owned.** Counters are produced by validator tools
and never accepted from the artifact.

Operationally: keep the coldkey offline, never mount the hotkey into any
container, and run owner operations through a scoped proxy from a multisig.

---

## Audit bundles

After each round the validator writes everything a third party needs to reach the
same ranking:

```
audit/round-000123/
  round.json      task manifest, sealed envelopes, every signed score artifact,
                  weight vectors, voided tasks, champion
  patches/        accepted patches for public-corpus tasks
  VERIFY.md       the commands to reproduce the round
  BUNDLE_HASH
```

Anyone can then re-derive the task set from the published block hash and confirm
it matches:

```bash
cf-corpus derive-round ./corpus \
    --block-hash 0x... --corpus-snapshot cf-corpus-2026.08
```

Publishing these is the credibility argument the network is actually making. A
leaderboard nobody can reproduce is a marketing artifact.

---

## Consensus upgrades

Every score is bound to a tuple:

```
(spec_version, spec_digest, toolchain_digest, corpus_snapshot, hardware_class)
```

Validators **refuse** to compare scores across different tuples. `spec.py` holds
every constant that two honest validators must agree on, and its digest changes if
any of them changes.

A validator running an outdated consensus specification must self-exclude rather
than emit incompatible weights. Upgrades are scheduled to an activation block,
announced publicly, and rehearsed on testnet — chain hyperparameters can only be
changed roughly once per two tempos, so at a 24-hour tempo a mistake costs two
days.

---

## Monitoring

Five numbers tell you whether the mechanism is healthy long before the leaderboard
does:

| Signal | Watch for |
|--------|-----------|
| Per-gate pass rates | A gate failing for everyone means a broken task package, not 40 bad miners |
| Tier A cross-validator dispersion | Rising dispersion at stable miner quality means measurement drift |
| vtrust trend | Falling vtrust with correct measurements means a consensus-spec mismatch |
| Cost per round | Rising cost against flat emissions is the exit condition |
| Corpus staleness | Scores rising on public tasks while hidden capture stays flat is overfitting |

That last row is the important one. It is the early-warning signal for benchmark
overfitting, and the response is a harder corpus refresh — not a burn.

---

## Troubleshooting

**"valgrind is not installed"** — The validator will not start. This is
deliberate: the deterministic tier is the consensus-bearing measurement.

**"Container runtime is runc, which shares the host kernel"** — Install gVisor or
Kata. `--sandbox.allow_unhardened_runtime` exists for local development and should
never be used on a machine holding a hotkey.

**"calibrated host requirements unmet"** — Tier B refuses to run. Either fix the
host per [above](#the-calibrated-wall-clock-host) or drop `--measurement.tier_b`;
the validator participates fully in consensus either way.

**Tasks voiding repeatedly** — Usually an unstable baseline. Run
`cf-corpus validate ./corpus` and check whether the benchmark is genuinely
deterministic: on a simulated CPU any variation across repeats means the program
is reading something it should not.

**vtrust falling while measurements look correct** — Check the consensus digest
against other validators. Different digests mean different scoring regimes, and
the weight vectors are not comparable.

**Round takes longer than the tempo** — Reduce `--neuron.public_tasks` or
`--measurement.fuzz_seconds`, or scale the verification fleet horizontally. The
next round starts immediately rather than accumulating drift.
