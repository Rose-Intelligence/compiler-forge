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
Set weights for 1 uids on mechanism 1
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
