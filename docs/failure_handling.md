# Failure handling

The rule this codebase is built around:

> A wrong number that looks right is worse than no number at all.

A validator that cannot measure something correctly is worth more to the network
silent than approximately right. This page is the contract that follows from
that, and where each part of it is enforced.

---

## Who gets blamed when something goes wrong

Most of the damage a system like this can do comes from attributing a failure to
the wrong party. There are four distinct outcomes, and they must never be
confused with one another.

| Outcome | Meaning | Effect on the miner | Effect on the round |
|---------|---------|---------------------|---------------------|
| **Gate failure** | The candidate genuinely failed a correctness gate | Zero for the task, reliability penalty | Round continues |
| **Honest null** | Every gate passed, no improvement found | Zero capture, **no penalty**, earns from the floor pool | Round continues |
| **Task voided** | The *task* is unmeasurable — unstable baseline, no hidden inputs, persistent tier divergence | Nothing. The task is dropped for everyone | No crown change |
| **Evaluation error** | *This validator* could not evaluate the pair | Nothing. No score is emitted for the pair | Round continues, error recorded |

The last two are the ones easy to get wrong, and both were real bugs found while
building this:

- A task package whose input generator failed used to yield an empty case list,
  which failed the differential gate for **every** miner. A corpus problem became
  forty undeserved zeroes. It now voids the task.
  → `RoundRunner._prepare_cases` returns `(cases, unpreparable)`.

- A crash inside the evaluator used to produce a zero-score artifact, punishing
  the miner for a fault on the validator's side. It now emits **no score** for
  that pair, marked `voided=True` so aggregation excludes it, and records the
  error so a validator crashing on everything is visible rather than looking like
  a round where nobody improved anything.
  → `RoundResult.evaluation_errors`.

Both are pinned by `tests/test_no_silent_failures.py`.

---

## A miner must not be able to void a task

Voiding removes a task from every miner's score **and** prevents a crown change
that round. Conflating "this patch cannot be measured" with "this task is broken"
would therefore hand any miner both a denial of service and a way for an
incumbent champion to defend itself indefinitely.

So the two are separate exceptions:

| Failure | Raises | Effect |
|---|---|---|
| The **baseline** cannot be measured | `TaskVoided` | Task dropped for everyone |
| The **candidate** cannot be measured | `CandidateUnmeasurable` | Zero for that artifact only |

The candidate's own determinism is checked before the two sides are compared, so
a non-deterministic patched build is attributed to the patch rather than to the
task.

## Chain operations

Every chain read and write goes through `compilerforge/chain/access.py`, so there
is exactly one place where "the chain said no" becomes an exception.

**A read that fails raises.** No read returns an empty list or a zero on failure.
A validator that cannot read the metagraph produces no weights.

**A rejected extrinsic raises.** The SDK reports failure in the returned result
rather than by raising, so a caller who ignored the return value would treat a
rejected weight submission as success. `_succeeded()` defaults to `False` for any
result shape it does not recognise — guessing "probably fine" is exactly how
weights get reported as set when they were not.

**A future block is refused.** `block_hash()` checks the head first. Asking for a
block that has not been produced is the specific mistake that would break the
freeze-before-entropy ordering the whole competition rests on.

**Both dataclass and mapping shapes are handled.** The SDK returns dataclasses
for some reads and plain dicts for others; assuming one shape would silently read
`None` from the other.

---

## Configuration

`NeuronConfig` raises on an unknown key rather than returning `None`.

This is not pedantry. A validator that read `config.neuron.tasks` as `None`
because the real name is `public_tasks` would evaluate zero tasks and report a
perfectly healthy round.

`--netuid` has no usable default: a neuron pointed at the wrong subnet reads the
wrong metagraph and sets weights nobody asked for, so `0` is rejected at startup.

> **Historical note.** Under SDK v10 this project used `bt.Config(parser)`, which
> silently discarded every argument the subnet defined — including `--netuid` —
> because CLI parsing was gated behind an environment variable that defaults to
> off. Configuration is parsed directly now, and
> `test_every_declared_argument_survives_parsing` exists so it cannot recur.

---

## Durable state

**A corrupt validator state file is fatal.** The champion registry and reliability
history are consensus-relevant. Starting "fresh" would hand the crown to whoever
scores highest next round, discarding the defender advantage that makes cloning
unprofitable — and it would do so invisibly. The neuron refuses to start and tells
the operator to restore the file or delete it deliberately.

**A corrupt baseline cache is rebuilt.** The cache is derived data, so this is
recoverable. It is still logged and the bad entry removed, because a cache that
keeps corrupting entries is a disk problem someone needs to see.

**A corrupt audit index is rebuilt from the round directories**, which are the
real record. The damaged file is preserved as `index.json.corrupt` rather than
overwritten, and the index is written atomically so a crash mid-write cannot
produce the problem it is recovering from.

---

## Measurement

**Zero instructions is an error, not a measurement.** A Callgrind profile
reporting no work would make every candidate look infinitely faster than the
baseline. `parse_callgrind_output` raises.

**A truncated cost line is never read as a measurement.** Valgrind 3.22 writes
`summary: 0` when instrumentation is gated; reading that as the result would
report every patch as doing nothing.

**Non-determinism is rejected, not averaged.** Repeated runs on a simulated CPU
must be identical. Variation means the benchmark read the clock, the PID or the
entropy pool, and averaging it would put irreproducible numbers into the weight
vector.

**Missing evidence contributes nothing, and is not renormalised.** A validator
without a calibrated wall-clock host reports a *smaller* score, not an inflated
one built from the components it does have. Where a measurement tool is missing
entirely — `/usr/bin/time` for peak memory — it is logged once with what is being
forfeited, rather than quietly scoring every candidate as having no memory
improvement.

---

## What miners are told

A miner who is told only "malformed report" cannot fix anything. `ArtifactRun`
carries `report_error` so the interface check can say *why* — the parse error, or
that no file was written at all.

The same applies to gates: every `GateResult` carries a `detail` explaining what
failed, and those details are published in the round's audit bundle.

---

## Deliberately loud, deliberately quiet

Not everything should raise. These are logged and continue, on purpose:

- **A malformed on-chain commitment** — the miner's to fix, expected during an
  interface transition. Debug level; the count that survived is logged.
- **One artifact failing to run** — one bad artifact must not end a round for
  everyone else. Logged with a traceback.
- **A failed weight heartbeat** — the heartbeat exists to keep the validator
  inside the activity cutoff, so it must survive a failed beat. Logged at error
  level, because persistent failure means drifting out of consensus.
- **A metagraph resync failure** — keeps the previous metagraph rather than
  turning a network blip into a crash loop. Logged at error level, because
  persistent failure means operating on stale data.

The distinction throughout: **recoverable and visible** is fine, **recoverable and
invisible** is not.
