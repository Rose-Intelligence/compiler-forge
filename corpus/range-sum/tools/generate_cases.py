#!/usr/bin/env python3
"""Differential input generator for the range-sum package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

The seed comes from the round's block hash, so the concrete inputs a candidate
is judged against did not exist when the artifact was frozen. Generation is a
pure function of the seed — two validators handed the same seed produce
byte-identical cases — so it uses an explicit counter-based hash rather than
``random``, whose stream is an implementation detail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


class Stream:
    """Deterministic byte stream derived from a seed and a counter."""

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_case(stream: Stream, index: int) -> dict:
    count = stream.next_int(40)
    # Values span negatives and zero and mix parities, so the sum and the
    # even-count observables both exercise their edges.
    values = [stream.next_int(400) - 200 for _ in range(count)]

    queries: list[str] = []
    for _ in range(1 + stream.next_int(8)):
        kind = stream.next_int(5)
        if kind == 0 and count:
            i = stream.next_int(count)
            queries.append(f"{i} {i}")                       # single element
        elif kind == 1 and count:
            queries.append(f"0 {count - 1}")                 # whole sequence
        elif kind == 2 and count:
            a = stream.next_int(count)
            b = stream.next_int(count)
            queries.append(f"{min(a, b)} {max(a, b)}")       # a sub-range
        elif kind == 3 and count:
            a = stream.next_int(count)
            queries.append(f"{a} {count + stream.next_int(8)}")  # hi past the end
        else:
            a = stream.next_int(count + 1)
            b = stream.next_int(count + 1)
            queries.append(f"{max(a, b)} {min(a, b)}")       # inverted / empty

    lines = [str(count), *(str(v) for v in values), *queries]
    text = "\n".join(lines) + "\n"
    return {
        "case_id": f"case-{index:05d}",
        "argv": [],
        "stdin_hex": text.encode().hex(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stream = Stream(args.seed)
    for i in range(args.count):
        case = make_case(stream, i)
        (args.out / f"{case['case_id']}.json").write_text(json.dumps(case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
