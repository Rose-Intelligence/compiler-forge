<!--
Keep the sections that apply and delete the rest. The consensus checklist is
not boilerplate: the items in it are the ones that make two validators
disagree, and disagreement is expensive to diagnose after the fact.
-->

## What this changes

<!-- One paragraph. What is different, and why it needed to change. -->

## Why this way

<!-- The alternative you rejected and the reason. Reviewers spend most of their
     time reconstructing this, so writing it down is the highest-value part of
     a PR description. -->

## Evidence

<!-- What you ran, and what it said. Not "tests pass" — the output. -->

```
```

---

## Consensus impact

- [ ] **No change to `compilerforge/spec.py`.**
      If this box is unchecked, the consensus digest changes and scores from
      before and after this PR are incomparable by design. That needs a
      `spec_version` bump, a scheduled activation block, and an announcement —
      not a merge alongside unrelated work.

- [ ] **No change to task selection determinism.**
      `evaluation/selection.py` must stay a pure function of
      `(block hash, corpus snapshot, spec digest)`. No wall-clock, no local
      randomness, no dependence on dict ordering or the `random` module's stream.

- [ ] **No change to how a measurement becomes a score.**
      If `evaluation/measurement.py` changed, a slow test proving two
      independent measurements of the same patch still agree *exactly* is
      included below.

- [ ] **The security boundary was not relaxed.**
      Additions to the `sandbox/isolation.py` deny-list are welcome.
      Relaxations need a written argument in this PR.

## Failure handling

- [ ] Every new failure path either raises or is logged with its reason.
      No new code path returns a plausible default on failure.
- [ ] A validator-side fault cannot score against a miner.
- [ ] A task-side fault voids the task rather than zeroing every miner.

<!-- See docs/failure_handling.md for why these three are called out
     specifically — each corresponds to a bug that actually shipped. -->

## Checks

- [ ] `ruff check compilerforge tests neurons`
- [ ] `pytest -m "not slow"`
- [ ] `pytest -m slow` — required if evaluation, measurement or the corpus changed
- [ ] `cf-corpus validate ./corpus` — required if any task package changed
