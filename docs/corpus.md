# The corpus

Where task packages live, what "hidden" actually means here, and what it cannot
mean on a permissionless network.

## Where packages are saved

Every package is a directory under [`corpus/`](../corpus), public and held-out
alike:

```
corpus/<package-id>/
  package.yaml            the manifest: build, test, benchmark, profiles, S_ref
  reference.patch         the expert optimization, measured, never shipped to agents
  repo/                   the source tree a candidate patches
  tools/generate_cases.py deterministic differential input generator
  inputs/hidden.sealed    optional: timelocked differential corpus
```

There is no second location and no database. A package is files on disk, the
corpus is a directory of them, and `Corpus.load(dir, snapshot)` is the whole
loading story. That is deliberate: the corpus has to be content-hashable so a
round seed can pin exactly which snapshot it was derived under, and a directory
of files hashes cleanly where a service does not.

## How they are served

Validators do not fetch tasks from an API. Each validator holds the corpus
locally, derives the round from a future block hash, and the derivation is a
pure function:

```
derive_round(seed, corpus, toolchain_digest) -> RoundPlan
```

Two validators with the same corpus snapshot and the same block hash derive the
identical task set, including which workload profile each package runs and the
128-bit per-task input seed. Nothing is coordinated at run time, so there is no
task server to compromise, rate-limit or take down.

`cf-corpus derive-round` re-derives any round from public data, which is what
makes "this artifact was not tuned to this task" checkable by a third party
rather than asserted.

## The three levels of concealment that exist

**1. Held-out packages — `hidden_family: true`.** The published manifest carries
a count and nothing else:

```json
{ "hidden_package_count": 3, "public_packages": [ ... ] }
```

A miner learns how many held-out families a round draws from, never which. Their
score contribution is weighted separately (`hidden_generalisation`, 0.10 of the
per-task score), so an artifact that scores well on them has transferred
technique rather than memorised a target.

**2. Unpublished profiles — `published: false`.** Concealment is per profile, not
only per package. A public package can carry a workload the manifest omits, so
even a package a miner can read has an axis they cannot see being tested on.
`record-pack` publishes two of its three profiles; `string-split` has a
`dense-unpublished` profile.

**3. Sealed inputs — `cf-corpus seal`.** The differential corpus is encrypted to a
future round of the drand quicknet beacon and published *now*. Before that round
nobody can decrypt it — including whoever sealed it. Afterwards anyone can open
it and re-run the audit. This is the only mechanism here that is cryptographic
rather than procedural.

## What "hidden from miners" cannot mean

**A permissionless validator set cannot keep a secret from miners.** Anyone can
register as a validator; a miner who wants the held-out packages can run one and
read them off disk. Any design that depends on validators holding material
miners cannot obtain is not permissionless — it is a licensing arrangement with
extra steps.

So the anti-overfitting guarantee here does not rest on secrecy. It rests on
three things that survive the adversary reading everything:

- **Selection postdates commitment.** The block hash choosing the repository,
  profile and input seed did not exist when the artifact digest was frozen.
  Knowing the whole corpus does not tell you which task you will get.
- **Inputs are generated per round.** `generate_cases.py` is seeded from the
  round, so there is no fixed input set to special-case against even for a
  package whose source you have memorised.
- **Timelock, not trust.** Sealed material is unreadable in advance by everyone
  and auditable afterwards by anyone.

Held-out packages are therefore best understood as *generalisation measurement*,
not as secrets. They raise the cost of overfitting; they do not make it
impossible. Documentation that claims otherwise is overclaiming, and the
distinction matters to a customer deciding whether to believe a benchmark.

### If you genuinely need a private set

Three options, in descending order of how well they fit this design:

1. **Grow the corpus until memorising it is not worth it.** The cheapest and most
   robust answer. Overfitting to 7 packages is tractable; to 200 it is not, and
   nothing about the mechanism has to change.
2. **Procedural generation.** Generate the *program* from the round seed, not just
   its inputs. Nothing needs hiding because the task did not exist before the
   round. This is the strongest option and the largest piece of work.
3. **A private corpus with a permissioned validator subset.** Workable, and it
   costs permissionlessness. If you take it, say so plainly rather than
   describing the network as open.

## Adding a package

```bash
cf-corpus validate corpus              # what would void a task at round time
cf-corpus measure-reference corpus/<id>  # fills every profile's s_ref
cf-eval patch corpus/<id> --patch corpus/<id>/reference.patch   # must be 1.000
```

`measure-reference` writes the measured `s_ref_deterministic` back into the
manifest. It has to be measured rather than estimated, because capture is
normalised against it: `capture = (S_lcb - 1) / (S_ref - 1)`.

Three things decide whether a package is worth adding:

**The inefficiency must survive `-O2`.** This is the most common mistake. A
`strlen` in a loop condition looks expensive and is free, because the compiler
proves the buffer unchanged and hoists it — the reference then measures 1.0000x
and the package is rejected. Every package here routes some step through a
separate translation unit (`keycmp.c`, `runbyte.c`, `fieldlen.c`, `accum.c`) so
that, with no LTO in the pinned toolchain, the optimizer cannot see through it.
What is measured is then the algorithm rather than the inliner.

**S_ref should be modest.** Capture divides by `S_ref - 1`, so a package whose
reference is worth 300x gives an agent that achieves 3x a capture of 0.007 —
all-or-nothing scoring dressed up as a gradient. Aim for the 1.2x–10x band the
existing packages occupy.

**The benchmark must not be in the patch scope.** If the measured code and the
patchable code are the same file, "computed less" and "computed faster" are
indistinguishable. Keep the hot code in `src/` and the benchmark in `bench/`.
