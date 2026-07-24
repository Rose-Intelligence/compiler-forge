# Running on mainnet

Read [running_on_testnet.md](running_on_testnet.md) first and complete its
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
sudo apt-get install -y clang cmake valgrind git
# plus gVisor or Kata — see docs/validator.md
cf-eval preflight
```

Every line of preflight should read green before you register.

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
