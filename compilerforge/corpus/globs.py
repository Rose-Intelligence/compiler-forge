"""Recursive path matching for the patch scope.

``PurePath.match`` treats ``**`` as a single-segment wildcard before Python 3.13,
so ``src/**`` matches ``src/a.c`` and *not* ``src/codec/a.c``. Every corpus
package happens to be flat, so nothing caught it — but any real project with
nested source directories has every nested file declared outside its own patch
scope, and a candidate that touches one is rejected for editing a file the
manifest says it owns.

The failure is closed rather than open, which is why it was survivable: the
mistake denies access it should have granted. It still makes the pipeline
unusable on ordinary project layouts.

One matcher is used by both the hygiene gate and the immutable-tree hash. They
must agree exactly: a file the gate calls patchable but the hash treats as
validator-owned would be rejected as a tree modification after being accepted as
a legal edit, and the two answers are computed in different modules.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import PurePosixPath

__all__ = ["matches_any", "matches"]


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    """Translate a glob into a regex, giving ``**`` its recursive meaning.

    Written out rather than delegated to ``fnmatch`` because fnmatch has no
    concept of a path separator: its ``*`` happily spans directories, which would
    make ``src/*`` match ``src/a/b.c`` and quietly widen every patch scope.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)

    while i < n:
        char = pattern[i]

        if char == "*":
            # "**/" or a trailing "**" spans any number of segments, including
            # none. A lone "*" stops at the separator.
            if pattern.startswith("**", i):
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
            else:
                i += 1
                out.append("[^/]*")
            continue

        if char == "?":
            out.append("[^/]")
            i += 1
            continue

        if char == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(char))
                i += 1
                continue
            body = pattern[i + 1 : close]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = close + 1
            continue

        out.append(re.escape(char))
        i += 1

    return re.compile(f"^{''.join(out)}$")


def matches(relative_path: str | PurePosixPath, pattern: str) -> bool:
    """Does one relative path match one glob?"""
    text = str(PurePosixPath(relative_path))
    if _compiled(pattern).match(text):
        return True
    # A bare directory pattern covers everything beneath it: "src" and "src/"
    # both mean the same thing to anyone writing a manifest by hand.
    trimmed = pattern.rstrip("/")
    if trimmed and not any(ch in trimmed for ch in "*?["):
        return text == trimmed or text.startswith(trimmed + "/")
    return False


def matches_any(relative_path: str | PurePosixPath, patterns: tuple[str, ...]) -> bool:
    return any(matches(relative_path, pattern) for pattern in patterns)
