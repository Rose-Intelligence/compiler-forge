#!/usr/bin/env python3
"""Differential input generator for the token-count package.

Called by the validator as::

    generate_cases.py --seed 0x... --count 500 --out DIR

Generation is a pure function of the seed, which comes from the round's block
hash. Two validators handed the same seed must produce byte-identical cases, so
this uses an explicit counter-based generator rather than the ``random`` module,
whose stream is an implementation detail that could change between versions.

The shapes here target what a data-structure rewrite gets wrong: repeated tokens,
tokens that collide in an obvious hash, tokens long enough to spill a small stack
buffer, and whitespace runs that a hand-rolled tokeniser mishandles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_WHITESPACE = [" ", "  ", "\t", "\n", " \t ", "\n\n"]


class Stream:
    """Deterministic byte stream derived from a seed and a counter."""

    def __init__(self, seed: str) -> None:
        self._seed = seed.encode()
        self._counter = 0

    def next_int(self, bound: int) -> int:
        self._counter += 1
        digest = hashlib.sha256(self._seed + self._counter.to_bytes(8, "big")).digest()
        return int.from_bytes(digest[:8], "big") % max(bound, 1)


def make_token(stream: Stream, vocabulary: int) -> str:
    kind = stream.next_int(12)
    if kind == 0:
        # Long enough to spill a fixed-size stack buffer in a rewritten tokeniser.
        length = 64 + stream.next_int(40)
        return "".join(_ALPHABET[stream.next_int(26)] for _ in range(length))
    if kind == 1:
        # Anagrams: identical character multiset, different words. A hash that
        # only sums bytes would collide these and merge their counts.
        return ["abc", "bca", "cab"][stream.next_int(3)]
    return f"w{stream.next_int(vocabulary)}"


def make_case(stream: Stream, index: int) -> dict:
    vocabulary = 4 + stream.next_int(60)
    tokens = 1 + stream.next_int(180)

    parts: list[str] = []
    for i in range(tokens):
        if i > 0:
            parts.append(_WHITESPACE[stream.next_int(len(_WHITESPACE))])
        parts.append(make_token(stream, vocabulary))

    # Sometimes lead or trail with whitespace, which a tokeniser must skip
    # without emitting an empty token.
    if stream.next_int(4) == 0:
        parts.insert(0, _WHITESPACE[stream.next_int(len(_WHITESPACE))])
    if stream.next_int(4) == 0:
        parts.append(_WHITESPACE[stream.next_int(len(_WHITESPACE))])

    text = "".join(parts)
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
