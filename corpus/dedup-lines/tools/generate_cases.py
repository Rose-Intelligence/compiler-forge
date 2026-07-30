#!/usr/bin/env python3
"""Differential input generator for the dedup-lines package. Pure function of the
seed (counter-based hash, not ``random``)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

_ALPHABET = "abcde"


class Stream:
    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_case(stream: Stream, index: int) -> dict:
    count = stream.next_int(40)
    # Small alphabet + short strings => many duplicates, including all-same and
    # all-distinct batches.
    lines = []
    for _ in range(count):
        length = 1 + stream.next_int(4)
        lines.append("".join(_ALPHABET[stream.next_int(5)] for _ in range(length)))

    text = "\n".join([str(count), *lines]) + "\n"
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
