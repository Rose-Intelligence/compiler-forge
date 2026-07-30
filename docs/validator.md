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
- [Running on testnet](#running-on-testnet)
- [Running on mainnet](#running-on-mainnet)

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
produce a round at all. The held-out families live outside the public corpus and
are provisioned with `--corpus.private_dir` (see Running); without them a round
fails closed rather than scoring on public tasks alone. `cf-corpus validate
./corpus --corpus.private_dir ./cf-corpus-private` checks the merged view.

---

## Running

```bash
python neurons/validator.py \
    --netuid <netuid> \
    --wallet.name validator --wallet.hotkey default \
    --subtensor.network finney \
    --corpus.dir ./corpus \
    --corpus.private_dir ./cf-corpus-private \
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
    --corpus.dir ./corpus --corpus.private_dir ./cf-corpus-private \
    --corpus.snapshot cf-corpus-2026.08
```

Key arguments:

| Argument | Default | Notes |
|----------|---------|-------|
| `--corpus.dir` | `./corpus` | The public task packages |
| `--corpus.private_dir` | — | The held-out packages, provisioned separately; merged with `--corpus.dir`. Required to run held-out tasks |
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
4. Produce    run every agent to produce its patch
5. Verify     independently evaluate every patch
6. Aggregate  combine into per-artifact standings
7. Crown      apply the dethronement rules
8. Weight     submit one vector per mechanism
9. Publish    write the audit bundle
```

Steps 1–3 are the security argument and **must not be reordered**. Because step 3
draws entropy that did not exist at step 1, "this artifact was not tuned to this
task" becomes checkable from public data rather than a claim anyone has to trust.

### Independent evaluation, merged by consensus

Every validator runs every agent and evaluates every `(artifact, task)` pair
itself — applies, builds, differentially tests, fuzzes, sanitizes and measures —
then sets weights from its own measurements. Yuma Consensus merges those weight
vectors by stake on chain, and that on-chain merge is the cross-validation: no
validator ever trusts another's patch or score, so there is nothing to forge and
no separate patch/score transport to run.

The redundancy is the point — independent measurement is what a validator is paid
for. Splitting production across validators to save the agent runs is a scale
optimisation for a large fleet, but it needs a signed patch/score exchange this
subnet does not yet ship, so every validator does the full evaluation.

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
absurd. Four techniques keep a round affordable, and all four are load-bearing.

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

Publishing these lets any third party reproduce the ranking rather than take it
on trust.

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

---

# Running on testnet

Testnet is where configuration mistakes are cheap. Mainnet is where they are not:
chain hyperparameters can only be changed roughly once per two tempos, so at a
24-hour tempo a wrong setting costs two days.

Rehearse everything here first.

Testnet netuid and endpoint are announced by the subnet operator. Substitute
`<netuid>` throughout.

---

## 1. Set up wallets

```bash
btcli wallet new_coldkey --wallet.name cf_test
btcli wallet new_hotkey  --wallet.name cf_test --wallet.hotkey miner
btcli wallet new_hotkey  --wallet.name cf_test --wallet.hotkey validator
```

Get testnet TAO from the faucet:

```bash
btcli wallet faucet --wallet.name cf_test --subtensor.network test
```

---

## 2. Register

```bash
btcli subnet register --netuid <netuid> \
    --wallet.name cf_test --wallet.hotkey miner \
    --subtensor.network test

btcli subnet register --netuid <netuid> \
    --wallet.name cf_test --wallet.hotkey validator \
    --subtensor.network test
```

Confirm both appear:

```bash
btcli subnet metagraph --netuid <netuid> --subtensor.network test
```

---

## 3. Install

```bash
sudo apt-get install -y clang cmake valgrind git
git clone https://github.com/Rose-Intelligence/compiler-forge
cd compiler-forge
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cf-eval preflight
pytest
```

---

## 4. Run a validator

```bash
python neurons/validator.py \
    --netuid <netuid> \
    --wallet.name cf_test --wallet.hotkey validator \
    --subtensor.network test \
    --corpus.dir ./corpus \
    --corpus.private_dir ./cf-corpus-private \
    --corpus.snapshot cf-corpus-testnet \
    --audit.dir ./audit \
    --neuron.epoch_length 360 \
    --neuron.public_tasks 2 \
    --neuron.hidden_tasks 1 \
    --neuron.freeze_lead_blocks 20 \
    --measurement.fuzz_seconds 10 \
    --sandbox.allow_unhardened_runtime
```

The differences from a mainnet configuration, and why:

| Flag | Testnet | Why |
|------|---------|-----|
| `--neuron.epoch_length 360` | ~72 min | Fast feedback. Mainnet needs a full day for a real round |
| `--neuron.public_tasks 2` | 2 | A round finishes in minutes rather than hours |
| `--neuron.freeze_lead_blocks 20` | 20 | ~4 minutes between freeze and task selection |
| `--measurement.fuzz_seconds 10` | 10 | Fuzzing dominates round time otherwise |
| `--sandbox.allow_unhardened_runtime` | set | Only acceptable because this hotkey holds nothing |

**Do not carry any of these to mainnet.** In particular, the unhardened-runtime
override should never be set on a machine holding a real hotkey.

---

## 5. Run a miner

Build and push the reference agent, or your own:

```bash
cd compilerforge/miner/reference_agent
docker build -t <your-registry>/cf-agent-test .
docker push <your-registry>/cf-agent-test

cf-miner check --image <your-registry>/cf-agent-test
```

Commit it:

```bash
python neurons/miner.py \
    --netuid <netuid> \
    --wallet.name cf_test --wallet.hotkey miner \
    --subtensor.network test \
    --artifact.image <your-registry>/cf-agent-test
```

---

## 6. Verify a full round

Watch the validator log for the sequence:

```
=== Round 1 ===
Froze 1 artifacts at block <N>
Waiting <k> blocks for the task-selecting hash
2 public + 1 hidden tasks from block <N+20>
Producing 3 of 3 assigned pairs
Set weights for 1 uids on mechanism 0
Published audit bundle sha256:...
```

Then check the results actually landed:

```bash
# Weights on chain
btcli subnet metagraph --netuid <netuid> --subtensor.network test

# The audit bundle
cat audit/round-000001/round.json | python -m json.tool | head -40

# Re-derive the task set from the published block hash — it must match
cf-corpus derive-round ./corpus \
    --block-hash $(python -c "import json;print(json.load(open('audit/round-000001/round.json'))['block_hash'])") \
    --corpus-snapshot cf-corpus-testnet \
    --public-tasks 2 --hidden-tasks 1
```

That last command is the one worth running. If the re-derived task manifest hash
does not match what the round published, the round was not what it claimed to be.

---

## 7. Checklist before mainnet

Each item should be **evidenced**, not assumed.

- [ ] A full round completes end to end and sets weights on both mechanisms
- [ ] `cf-corpus validate ./corpus` passes, with at least one held-out family
- [ ] Every package has a measured `s_ref_deterministic` for every profile
- [ ] The reference patch scores capture ≈ 1.000 on every package
- [ ] A seeded behaviour-changing patch is caught by the gate that should catch it
- [ ] A patch that passes public tests but breaks on hidden inputs fails `differential`
- [ ] Two independent hosts produce **identical** Tier A instruction counts
- [ ] A third party re-derives the task set from the audit bundle and matches
- [ ] `pytest -m slow` passes on the validator host
- [ ] The heartbeat is confirmed running and beats the activity cutoff
- [ ] A hardened container runtime is installed and `allow_unhardened_runtime` is **not** set
- [ ] The coldkey is offline; the hotkey is never mounted into a container
- [ ] Round cost is *measured*, not estimated, and is compatible with expected emissions

The Tier A cross-host check deserves emphasis. If two honest validators cannot
agree on a measurement within the published tolerance, consensus will decay
regardless of miner quality, and no amount of good scoring design recovers it.

---

## Troubleshooting

**"Hotkey is not registered"** — Registration did not land, or you are pointed at
the wrong network. Check `btcli subnet metagraph --netuid <netuid> --subtensor.network test`.

**Round produces no weights** — Usually no artifact commitments at the freeze
block. The validator logs `No artifact commitments at block N`. Commit a miner
artifact and wait for the next round.

**"corpus contains no public packages"** — `--corpus.dir` is wrong, or the
packages lack `package.yaml`.

**Tasks void immediately** — Run `cf-corpus validate ./corpus`. Most often a
missing `s_ref_deterministic`, or a benchmark that is not deterministic under
Callgrind.

**Weights set but vtrust stays at zero** — Expect this until other validators
appear. On a single-validator testnet there is nothing to reach consensus with.

---

# Running on mainnet

Read the testnet section above first and complete its
checklist. Nothing below assumes you skipped it.

---

## For miners

### 1. Register

Registration costs a recycled TAO amount that varies with demand. Read it live:

```bash
btcli subnet register --netuid <netuid> \
    --wallet.name miner --wallet.hotkey default \
    --subtensor.network finney
```

New registrations enter an immunity period during which they cannot be pruned. Use
it: an artifact that has never been scored is an artifact that cannot defend its
UID.

### 2. Push and commit

```bash
cf-miner check --image ghcr.io/you/my-optimizer
docker push ghcr.io/you/my-optimizer

cf-miner submit --netuid <netuid> \
    --image ghcr.io/you/my-optimizer \
    --wallet.name miner --wallet.hotkey default

cf-miner status --netuid <netuid> --wallet.name miner --wallet.hotkey default
```

### 3. Run the neuron

```bash
pm2 start neurons/miner.py --name cf-miner --interpreter python3 -- \
    --netuid <netuid> \
    --wallet.name miner --wallet.hotkey default \
    --subtensor.network finney \
    --artifact.image ghcr.io/you/my-optimizer
```

Your artifact competes from the next round whose task-selecting block postdates
your commitment.

---

## For validators

### 1. Hardware

Do not skip the hardened container runtime. This machine holds a hotkey and runs
anonymous code submitted by competitors.

```bash
sudo apt-get install -y clang cmake valgrind git time
# plus gVisor or Kata — see docs/validator.md
cf-validator preflight --netuid <netuid>
```

Preflight must exit 0 before you register. A `BLOCKING` line means this host
would either refuse to start or produce scores nobody else can reproduce.

### 2. Register and acquire stake

```bash
btcli subnet register --netuid <netuid> \
    --wallet.name validator --wallet.hotkey default \
    --subtensor.network finney

btcli stake add --netuid <netuid> --amount <tao> \
    --wallet.name validator --wallet.hotkey default
```

Validator permits go to the top neurons by stake weight, recalculated every epoch,
with a threshold below which a validator is zeroed. A new subnet has no validators
and the owner gets no special treatment; the standard approach is asking an
established root validator to parent your hotkey as a childkey via `set-children`,
lending stake weight until the subnet attracts its own.

### 3. Run

```bash
pm2 start neurons/validator.py --name cf-validator --interpreter python3 -- \
    --netuid <netuid> \
    --wallet.name validator --wallet.hotkey default \
    --subtensor.network finney \
    --corpus.dir ./corpus \
    --corpus.private_dir ./cf-corpus-private \
    --corpus.snapshot cf-corpus-2026.08 \
    --audit.dir ./audit
```

Add `--measurement.tier_b --measurement.tier_b_affinity 4-7` **only** on a
properly calibrated bare-metal host. A validator without one still carries a full
consensus weight; a validator reporting wall-clock from a shared machine is
reporting noise.

### 4. Confirm liveness

```bash
btcli subnet metagraph --netuid <netuid>
```

Watch `updated` for your UID. With a day-long round, the heartbeat is what keeps
you inside the activity cutoff between scoring runs — the neuron runs it
automatically, and if it stops you fall out of consensus for reasons unrelated to
your measurements.

---

## For the subnet owner

### Registration is capital, not a fee

The entire lock transfers into the new subnet's pool as its initial TAO reserve.
It is recovered only by owning a subnet that earns. Read the price live — it
doubles on each network-wide registration and decays over roughly two weeks, so
entry timing is a genuine cost lever:

```bash
btcli query subnet-registration-cost --json
```

New subnets are immune from price-ranked deregistration for about six months.
**That immunity window is the entire runway.** After it expires, the subnet with
the lowest EMA alpha price is pruned whenever someone new registers. A subnet that
registers first and builds afterwards spends its runway debugging.

### Registration

```bash
# The owner coldkey should already be a multisig
btcli tx register-subnet --dry-run -w cf_owner
btcli tx register-subnet -w cf_owner

btcli query subnet-start-schedule --netuid <netuid>
btcli tx start-call --netuid <netuid> -w cf_owner

# Two different parties hold these two flags
btcli sudo get --netuid <netuid> --name subnet_is_active
btcli query subnet-emission-enabled --netuid <netuid>
```

Emission is off at registration and the switch belongs to root, not to owners. Plan
for a testing window with a live, active, zero-emission subnet before anything
economic happens.

### Hyperparameters

```bash
btcli sudo set --netuid <netuid> --param tempo --value 7200
btcli sudo set --netuid <netuid> --param commit_reveal_weights_enabled --value true
btcli sudo set --netuid <netuid> --param max_allowed_uids --value 128
btcli sudo set --netuid <netuid> --param immunity_period --value 14400
```

| Parameter | Value | Why |
|-----------|-------|-----|
| `tempo` | 7200 | A full round — build, differential, fuzz, sanitize, measure across a corpus — cannot complete in 72 minutes |
| `commit_reveal_weights_enabled` | true | Weight copying is the one failure that makes independent measurement worthless |
| `max_allowed_uids` | 128 | Required: `max_allowed_uids × mechanism_count ≤ 256`, and this subnet runs two |
| `immunity_period` | 14400 | A new miner must survive one full round before it can be pruned |
| `recycle_or_burn` | recycle | Anything directed at burn UIDs should return to the pool rather than leave the system |

Verify the activity cutoff explicitly at your chosen tempo. Effective cutoff is
`factor × tempo / 1000`, bounded to 1,000–50,000 blocks. At a long tempo this
must be checked, or validators between scoring runs are silently excluded.

```bash
python -c "
from compilerforge.chain.hyperparameters import effective_activity_cutoff, heartbeat_required
print('cutoff blocks:', effective_activity_cutoff(7200))
print('heartbeat required:', heartbeat_required(7200))
"
```

### Two mechanisms

```bash
btcli tx set-mechanism-count --netuid <netuid> --count 2 -w cf_owner
# emission split as u16 weights summing to 65535
#   mechanism 0 : Generalist Agent Championship   = 39321   (60%)
#   mechanism 1 : Specialist cells + bounty lane  = 26214   (40%)
```

A subnet is created with one mechanism; the second exists only after this call.
Until it does, a validator reads the live mechanism count and folds the
specialist/bounty vector into mechanism 0, scaled by its emission share, so a
miner whose value is specialisation still earns rather than having its scored
weight silently discarded. Once the second mechanism is created the two vectors
separate again with no validator change.

### Owner discipline

- **Multisig coldkey from registration.** Migrating later requires an announced
  coldkey swap, which is strictly more painful. Grant a dedicated operations key a
  narrow proxy for routine hyperparameter changes.
- **Run a validator on the owner hotkey.** The owner cut and validation rewards
  land on the same key.
- **Do not mine on the owner hotkey.** Miner emission directed at owner hotkeys is
  never paid, and it degrades the subnet's own emission share.
- **Never substitute an operator-computed weight vector.** Validators score
  locally and sign what they measured. Operator-computed weights that validators
  merely relay are a known credibility failure, and this software does not support
  it.
- **Publish every round bundle.** The audit repository is the credibility argument
  the network is actually making.

### Changing hyperparameters later

Each parameter can be changed roughly once per two tempos, tracked independently.
Owner admin calls are rejected during the last few blocks of each tempo while the
epoch is computed. At a 24-hour tempo that is a two-day cooldown per parameter —
plan upgrade windows days ahead, announce them, and rehearse on testnet.

Verify the live configuration against what this software expects:

```bash
python -c "
from compilerforge.chain.access import ChainAccess
from compilerforge.chain.hyperparameters import check_live
chain = ChainAccess(netuid=<netuid>, network='finney')
for problem in check_live(chain) or ['configuration matches the plan']:
    print('-', problem)
"
```

A failure to read is itself reported as a discrepancy. "We could not check" and
"everything is fine" never look the same.

---

## Consensus upgrades

Every score is bound to `(spec_version, spec_digest, toolchain_digest,
corpus_snapshot, hardware_class)`, and validators refuse to compare across
differing tuples.

Upgrading the consensus specification is a scheduled, announced, network-wide
switch at an activation block. A validator running an outdated specification must
self-exclude rather than emit incompatible weights — which it does automatically,
because the round seed carries the digest and derivation refuses on mismatch.

Never change the specification during a live competition.
