# Contributing

## Where help is most valuable

**Task packages.** The corpus is the network's whole substance. A good package is
real code with a real inefficiency, a reference optimization that measurably fixes
it, and hidden inputs that catch what the public tests miss. See
[adding a task package](../docs/architecture.md#adding-a-task-package).

**Equivalence comparators.** Writing one good comparator is easy. Writing thirty,
across seven workload families, such that none can be gamed and none rejects a
legitimate optimization, is genuinely hard and is where the real design work is.

**Adversarial testing.** Seed a patch that should be caught, and check it is
caught by the gate that should catch it — not by luck at a later gate. If you find
a patch that passes every gate and changes behaviour, that is the most valuable
bug report this project can receive.

**Measurement calibration.** Tier A agreement across heterogeneous hosts is the
property consensus rests on. Reports of hosts that disagree are important.

## Before opening a pull request

```bash
ruff check compilerforge tests
ruff format --check compilerforge tests
pytest
pytest -m slow          # if you touched evaluation, measurement or the corpus
```

## Changes that need extra care

**Anything in `compilerforge/spec.py`** changes the consensus digest. Scores from
before and after become incomparable — by design. Such a change needs a
`spec_version` bump, an activation block, and an announcement. Do not slip one in
alongside unrelated work.

**Anything in `evaluation/selection.py`** must stay a pure function of
`(block hash, corpus snapshot, consensus digest)`. No wall-clock, no local
randomness, no dependence on dict ordering or the `random` module's stream.

**Anything in `evaluation/measurement.py`** decides scores. Changes need a slow
test proving two independent measurements of the same patch still agree exactly.

**Anything in `sandbox/isolation.py`** is the boundary between anonymous code and
a machine holding a hotkey. Additions to the deny-list are welcome; relaxations
need a written argument.

## Reporting a security issue

Do not open a public issue for a sandbox escape, a way to read hidden task
material, or a way to forge a measurement. Contact the maintainers privately
first.

## Style

See [STYLE.md](STYLE.md).
