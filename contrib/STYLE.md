# Style

Formatting is `ruff` with a 110-column line length. Run `ruff format` and move on;
formatting is not worth discussing.

What follows is about the parts a formatter cannot check.

## Comments explain why, not what

The code already says what it does. A comment earns its place by explaining a
decision the reader would otherwise second-guess, or a constraint that is not
visible locally.

```python
# Bad — restates the code
# Loop over the candidates
for candidate in candidates:

# Good — explains a decision
# Ordered cheapest-first: most candidates die at the build gate, and fuzzing
# one that was never going to apply cleanly is pure waste.
```

Load-bearing comments in this codebase usually explain one of three things: why a
value must be deterministic, why something fails closed rather than degrading, or
what attack a check exists to prevent. Those are worth writing at length.

## Fail closed, and say why

An error message is a debugging session someone does not have to have. State what
went wrong, why it matters, and what to do.

```python
# Bad
raise MeasurementError("callgrind failed")

# Good
raise MeasurementError(
    "callgrind reported no instructions. Either the benchmark never reached its "
    "measured region, or the binary was built without the instrumentation "
    "markers the task contract declares."
)
```

Never return a plausible default where the honest answer is "I do not know". A
validator that cannot measure something correctly is worth more silent than
approximately right.

## Name the thing, not its type

`capture`, `s_ref`, `speedup_lcb`, `frozen_at_block` — the domain has precise
vocabulary and the code should use it. `result`, `data`, `value` and `info` are
almost never the best available name.

## Tests read as claims

A test name should state what must be true, not which function it calls.

```python
# Bad
def test_capture_calculation():

# Good
def test_a_near_copy_cannot_dethrone_the_champion():
def test_wall_clock_noise_alone_cannot_contradict_the_deterministic_tier():
def test_a_patch_that_does_nothing_is_an_honest_null_not_a_failure():
```

If the name needs a comment to explain what it means, the name is wrong.

## Type hints on public surfaces

Every public function, every dataclass field, every model. Internal helpers can be
inferred. `from __future__ import annotations` at the top of every module.

## No silent truncation

If something is capped, sampled or dropped, log it. Silent truncation reads as
"covered everything" when it did not.

## Docstrings on modules

Every module opens with what it is for and, where it matters, why it works the way
it does. A reader landing in `measurement.py` should learn why wall-clock is not
the consensus metric before reading a line of code.
