---
name: Accepted behaviour-changing patch
about: A patch passed every gate and still changed what the program does
title: "correctness: "
labels: correctness, security, priority-high
---

<!--
This is the most valuable bug report the project can receive. The entire
proposition is that a patch which reaches a score preserved the declared
behaviour; a counter-example is a hole in the gate sequence, not a bad round.

If the patch also reached a customer or a merged pull request, treat it as a P0
and say so at the top.
-->

## What behaviour changed

<!-- Concretely: an input, the baseline's observable output, the patched
     program's observable output. -->

**Input:**

**Baseline produced:**

**Patched produced:**

## Which gate should have caught it

- [ ] `differential` — the hidden inputs did not cover this case
- [ ] `public_tests` — the project's own suite has a gap
- [ ] `asan` / `ubsan` — a memory or UB fault went undetected
- [ ] `second_opt_level` — behaviour is optimization-level dependent
- [ ] `api_abi` — a signature or ABI change slipped through
- [ ] None of the above — the equivalence contract itself is wrong

<!-- The last box matters most. If the declared discipline permitted this
     change, the package's contract is the bug, not the gate. -->

## The patch

```diff
```

## Task and round

- Package / profile:
- Task id:
- Artifact digest:
- Round / block:

## Reproduction

```bash
cf-eval patch corpus/<package> --patch <patch> --seed <seed>
```

## Scope

- [ ] The patch was only ever scored on the public corpus
- [ ] The patch was published in a round bundle
- [ ] The patch reached a real repository or a customer — **P0**
