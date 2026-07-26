#!/usr/bin/env python3
"""Differential input generator for the sorted-index package.

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

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


class Stream:
    """Deterministic byte stream derived from a seed and a counter."""

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_key(stream: Stream) -> str:
    # Short keys and a small alphabet, so shared prefixes and exact duplicates
    # both occur often. Those are the cases a bounded search gets wrong first.
    length = 1 + stream.next_int(6)
    return "".join(_ALPHABET[stream.next_int(6)] for _ in range(length))


def make_case(stream: Stream, index: int) -> dict:
    count = stream.next_int(40)
    # si_build's contract is sorted input, so the generator sorts. Duplicates
    # are kept deliberately: lookup must return the first of a run.
    keys = sorted(make_key(stream) for _ in range(count))

    queries: list[str] = []
    for _ in range(1 + stream.next_int(8)):
        kind = stream.next_int(4)
        if kind == 0 and keys:
            queries.append(keys[stream.next_int(len(keys))])   # a present key
        elif kind == 1 and keys:
            queries.append(keys[stream.next_int(len(keys))][:1])  # a prefix of one
        elif kind == 2:
            queries.append("")                                  # matches everything
        else:
            queries.append(make_key(stream))                    # usually absent

    lines = [str(count), *keys, *queries]
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
