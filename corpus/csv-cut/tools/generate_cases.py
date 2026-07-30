#!/usr/bin/env python3
"""Differential input generator for the csv-cut package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

The seed comes from the round's block hash, so the concrete inputs did not exist
when the artifact was frozen. Generation is a pure function of the seed — two
validators handed the same seed produce byte-identical cases — so it uses an
explicit counter-based hash rather than ``random``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

_ALPHABET = "abcdefghij"


class Stream:
    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_case(stream: Stream, index: int) -> dict:
    fields = 1 + stream.next_int(12)
    parts = []
    for _ in range(fields):
        # Widths include zero, so empty fields — the boundary the offset table
        # must get right — occur often.
        width = stream.next_int(5)
        parts.append("".join(_ALPHABET[stream.next_int(10)] for _ in range(width)))
    row = ",".join(parts)

    queries: list[str] = []
    for _ in range(1 + stream.next_int(8)):
        kind = stream.next_int(4)
        if kind == 0:
            queries.append("0")                          # first field
        elif kind == 1:
            queries.append(str(fields - 1))              # last field
        elif kind == 2:
            queries.append(str(stream.next_int(fields))) # some field
        else:
            queries.append(str(fields + stream.next_int(4)))  # out of range

    lines = [row, *queries]
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
