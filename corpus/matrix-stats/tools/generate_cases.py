#!/usr/bin/env python3
"""Differential input generator for the matrix-stats package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

and writes one JSON manifest per case. The seed comes from the round's block
hash, so the concrete inputs a candidate is judged against did not exist when
the artifact was frozen. There is no fixed input set to special-case against.

Generation is a pure function of the seed. Two validators handed the same seed
must produce byte-identical cases, so this uses an explicit counter-based
generator rather than the ``random`` module, whose stream is an implementation
detail that could change between Python versions.
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
    """One case is a shape and a fill seed.

    Shapes deliberately include the ones a careless restructuring breaks first:
    a single row (variance is zero everywhere), a single column, and shapes
    where rows and cols are coprime so a flattened index cannot be assumed to
    line up with a row boundary.
    """
    kind = stream.next_int(10)
    if kind == 0:
        rows, cols = 1, 1 + stream.next_int(16)
    elif kind == 1:
        rows, cols = 1 + stream.next_int(40), 1
    elif kind == 2:
        rows, cols = 7, 13
    else:
        rows = 1 + stream.next_int(60)
        cols = 1 + stream.next_int(24)

    seed = stream.next_int(2**31)
    return {
        "case_id": f"case-{index:05d}",
        "argv": [],
        "stdin_hex": f"{rows} {cols} {seed}\n".encode().hex(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stream = Stream(args.seed)
    for index in range(args.count):
        case = make_case(stream, index)
        (args.out / f"{case['case_id']}.json").write_text(json.dumps(case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
