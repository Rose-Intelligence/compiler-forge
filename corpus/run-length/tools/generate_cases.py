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

_ALPHABET = "abcd"


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
    """One line of run-heavy text.

    Run lengths straddle 255 deliberately: that is where the encoder has to
    split a run into two pairs, and it is the first thing a rewritten encoder
    gets wrong.
    """
    parts: list[str] = []
    for _ in range(stream.next_int(12)):
        char = _ALPHABET[stream.next_int(len(_ALPHABET))]
        kind = stream.next_int(6)
        if kind == 0:
            run = 250 + stream.next_int(12)   # straddles the 255 split
        elif kind == 1:
            run = 1
        else:
            run = 1 + stream.next_int(40)
        parts.append(char * run)

    text = "".join(parts)
    return {
        "case_id": f"case-{index:05d}",
        "argv": [],
        "stdin_hex": (text + "\n").encode().hex(),
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
