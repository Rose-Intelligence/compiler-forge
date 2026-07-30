#!/usr/bin/env python3
"""Differential input generator for the sparse-dot package. Pure function of the seed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


class Stream:
    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_case(stream: Stream, index: int) -> dict:
    n = 1 + stream.next_int(30)
    # Sparse vector: ~20% nonzero, so zeros — the positions the fast path skips —
    # dominate.
    vec = [(stream.next_int(19) - 9) if stream.next_int(5) == 0 else 0 for _ in range(n)]

    lines = [str(n), " ".join(str(x) for x in vec)]
    for _ in range(1 + stream.next_int(6)):
        # Dense-ish queries, including some all-zero to exercise the empty overlap.
        if stream.next_int(4) == 0:
            q = [0] * n
        else:
            q = [(stream.next_int(11) - 5) for _ in range(n)]
        lines.append(" ".join(str(x) for x in q))

    text = "\n".join(lines) + "\n"
    return {"case_id": f"case-{index:05d}", "argv": [], "stdin_hex": text.encode().hex()}


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
