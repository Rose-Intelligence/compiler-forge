#!/usr/bin/env python3
"""Differential input generator for the string-split package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

and writes one JSON manifest per case. The seed comes from the round's block
hash, so the concrete inputs a candidate is judged against did not exist when
the artifact was frozen. That is what makes special-casing unprofitable: there
is no fixed input set to special-case against.

Generation is a pure function of the seed. Two validators handed the same seed
must produce byte-identical cases, or they are not measuring the same thing —
so this uses an explicit counter-based generator rather than the ``random``
module, whose stream is an implementation detail that could change between
Python versions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

# Shapes chosen to exercise the parts of the contract a careless optimization
# breaks first: empty fields, delimiters at the edges, whitespace that must be
# trimmed, and lines long enough that a quadratic length scan is visible.
_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class Stream:
    """Deterministic byte stream derived from a seed and a counter."""

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)

    def choice(self, items):
        return items[self.next_int(len(items))]


def make_field(stream: Stream) -> str:
    kind = stream.next_int(10)
    if kind == 0:
        return ""  # empty field
    if kind == 1:
        return " " * (1 + stream.next_int(3))  # whitespace only
    length = 1 + stream.next_int(12)
    body = "".join(_ALPHABET[stream.next_int(len(_ALPHABET))] for _ in range(length))
    lead = " " * stream.next_int(3)
    trail = " " * stream.next_int(3)
    return lead + body + trail


def make_line(stream: Stream) -> str:
    fields = 1 + stream.next_int(14)
    line = ",".join(make_field(stream) for _ in range(fields))
    edge = stream.next_int(12)
    if edge == 0:
        line = "," + line  # leading delimiter
    elif edge == 1:
        line = line + ","  # trailing delimiter
    return line


def make_case(stream: Stream, index: int) -> dict:
    lines = 1 + stream.next_int(24)
    text = "\n".join(make_line(stream) for _ in range(lines)) + "\n"
    return {
        "case_id": f"case-{index:05d}",
        "argv": [],
        "stdin_hex": text.encode().hex(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stream = Stream(args.seed)

    for i in range(args.count):
        case = make_case(stream, i)
        (args.out / f"{case['case_id']}.json").write_text(json.dumps(case))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
