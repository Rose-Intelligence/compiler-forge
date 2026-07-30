#!/usr/bin/env python3
"""Differential input generator for the box-sum package. Pure function of the seed."""

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
    rows = 1 + stream.next_int(6)
    cols = 1 + stream.next_int(6)
    grid = [[stream.next_int(40) - 20 for _ in range(cols)] for _ in range(rows)]

    lines = [f"{rows} {cols}"]
    lines += [" ".join(str(v) for v in row) for row in grid]

    for _ in range(1 + stream.next_int(8)):
        r0 = stream.next_int(rows + 1)
        r1 = stream.next_int(rows + 2)
        c0 = stream.next_int(cols + 1)
        c1 = stream.next_int(cols + 2)
        lines.append(f"{min(r0, r1)} {min(c0, c1)} {max(r0, r1)} {max(c0, c1)}")

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
