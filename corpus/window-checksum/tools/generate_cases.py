#!/usr/bin/env python3
"""Differential input generator for the run-length package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

The seed comes from the round's block hash, so the concrete inputs a candidate
is judged against did not exist when the artifact was frozen.

Generation is a pure function of the seed: two validators handed the same seed
must produce byte-identical cases. That is why this uses an explicit
counter-based hash rather than ``random``, whose stream is an implementation
detail that can change between Python versions.
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
    """A hex byte string.

    Short buffers are generated on purpose, including ones shorter than the
    widths the harness probes: a rolling implementation has to agree with the
    resumming one about windows that do not exist. High bytes are over-sampled
    so sums cross the 65521 modulus, which is where a careless subtraction
    underflows.
    """
    length = stream.next_int(60)
    data = bytes(
        255 - stream.next_int(6) if stream.next_int(3) == 0 else stream.next_int(256)
        for _ in range(length)
    )
    return {
        "case_id": f"case-{index:05d}",
        "argv": [],
        "stdin_hex": (data.hex() + "\n").encode().hex(),
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
