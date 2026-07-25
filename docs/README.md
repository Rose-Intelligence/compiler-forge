# Documentation

## Start here

| If you are… | Read |
|-------------|------|
| Building an optimization agent | [miner.md](miner.md) |
| Running a validator | [validator.md](validator.md) |
| Deciding whether the mechanism is sound | [incentive_mechanism.md](incentive_mechanism.md) |
| Reading or extending the code | [architecture.md](architecture.md) |
| Wondering what happens when something breaks | [failure_handling.md](failure_handling.md) |
| Deploying | [running_on_testnet.md](running_on_testnet.md) → [running_on_mainnet.md](running_on_mainnet.md) |

## The short version

Miners submit immutable optimization agents as container images pinned by digest.
Validators run those agents against repositories the miner has never seen, verify
that the resulting patches preserve behaviour, measure what they cost, and pay
only for improvements they reproduced themselves.

The measurement problem — that no two validators own the same hardware — is solved
by scoring on **instruction counts from a simulated CPU**, which vary by roughly a
millionth of a percent across machines, rather than on wall-clock, which varies by
about 2.7% on the same machine.

## Reference

- **Consensus constants** — [`compilerforge/spec.py`](../compilerforge/spec.py).
  Every number two validators must agree on. Changing any of them is a fork.
- **Hardware requirements** — [`min_compute.yml`](../min_compute.yml)
- **Contributing** — [`contrib/CONTRIBUTING.md`](../contrib/CONTRIBUTING.md),
  [`contrib/STYLE.md`](../contrib/STYLE.md)

## Command line

```bash
cf-eval preflight                  # what can this machine measure?
cf-eval gates                      # what must a patch survive?
cf-eval spec                       # the active consensus constants
cf-eval patch <package> --patch <diff>
cf-eval agent <package> --entrypoint "<command>"
cf-eval write-task <package>       # a task.json to develop against

cf-corpus validate <dir>
cf-corpus measure-reference <package>
cf-corpus manifest <dir> --snapshot <id>
cf-corpus derive-round <dir> --block-hash 0x... --corpus-snapshot <id>
cf-corpus seal <package> --hours 36

cf-validator preflight --netuid <n>       # can this host validate?
cf-validator hyperparameters --netuid <n> # is the subnet configured right?

cf-miner check  --image <repo>
cf-miner submit --netuid <n> --image <repo>
cf-miner status --netuid <n>

python neurons/miner.py     --help
python neurons/validator.py --help
```
