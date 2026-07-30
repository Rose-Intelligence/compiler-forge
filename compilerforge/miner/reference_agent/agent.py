"""A minimal optimization agent — the starting point a miner builds from.

It reads the task and the repository, applies one behaviour-preserving speedup,
checks that the code still compiles, and writes the patch. It does not profile,
self-measure, or call a model: the validator's measurement is authoritative, so an
agent's only job is to produce a correct, faster patch.

This is deliberately basic. To compete, extend it — add transforms, profile to
find where they pay off, or drive candidate generation with the model the
validator exposes over its metered proxy.

Contract: read ``/task/task.json`` and ``/workspace/repo``; write
``/output/patch.diff`` and ``/output/report.json``. The paths are overridable
through the ``CF_LOCAL_*`` environment variables for a local run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

TASK = Path(os.getenv("CF_LOCAL_TASK", "/task")) / "task.json"
REPO = Path(os.getenv("CF_LOCAL_REPO", "/workspace/repo"))
OUTPUT = Path(os.getenv("CF_LOCAL_OUTPUT", "/output"))
WORKDIR = Path(os.getenv("CF_AGENT_WORKDIR", "/tmp/cf-agent"))

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx"}

_HEADER = re.compile(
    r"^(?P<indent>[ \t]*)for\s*\(\s*(?P<init>[^;]*?)\s*;\s*"
    r"(?P<var>\w+)\s*<\s*strlen\s*\(\s*(?P<arg>\w+)\s*\)\s*;(?P<rest>[^\n]*)$",
    re.MULTILINE,
)

_REALLOC = re.compile(
    r"realloc\s*\(\s*(?P<ptr>\w+)\s*,\s*\+\+\s*(?P<count>\w+)\s*\*\s*sizeof"
)


def avoid_repeated_realloc(source: str) -> str | None:
    """Grow a buffer geometrically instead of one element at a time.

    ``realloc(p, ++n * sizeof(T))`` in a loop reallocates and copies on every
    element, which is quadratic. Doubling the capacity makes it amortised linear.
    Returns the transformed source, or ``None`` when the pattern is absent.
    """
    if not _REALLOC.search(source):
        return None
    return _REALLOC.sub(
        lambda m: (
            f"realloc({m.group('ptr')}, "
            f"(++{m.group('count')} < 8 ? 8 : {m.group('count')} * 2) * sizeof"
        ),
        source,
    )


def hoist_loop_invariant_strlen(source: str) -> str | None:
    """Bind a loop-invariant ``strlen`` once instead of re-walking the string.

    ``for (i = 0; i < strlen(s); i++)`` recomputes the length on every iteration,
    turning a linear scan into a quadratic one. Rewritten only when the loop body
    does not modify the string, which would make the cached length wrong. Returns
    the transformed source, or ``None`` when it does not apply.
    """
    for match in _HEADER.finditer(source):
        body = _loop_body(source, match.end())
        if body is None or _writes_to(match.group("arg"), body):
            return None

    def replace(match: re.Match[str]) -> str:
        length = f"cf_len_{match.group('arg')}"
        return (
            f"{match.group('indent')}const size_t {length} = strlen({match.group('arg')});\n"
            f"{match.group('indent')}for ({match.group('init')}; "
            f"{match.group('var')} < {length};{match.group('rest')}"
        )

    changed, count = _HEADER.subn(replace, source)
    return changed if count else None


def _loop_body(source: str, start: int) -> str | None:
    """The brace-delimited body of the loop whose header ends at ``start``."""
    brace = source.find("{", start)
    if brace == -1:
        return None
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    return None


def _writes_to(name: str, body: str) -> bool:
    """Whether ``body`` might modify the string ``name`` (so the length can change)."""
    escaped = re.escape(name)
    return bool(
        re.search(rf"\b{escaped}\s*\[[^\]]*\]\s*=[^=]", body)
        or re.search(rf"\*\s*{escaped}\b\s*=[^=]", body)
        or re.search(rf"\bstr(?:cpy|cat|ncpy|ncat)\s*\(\s*{escaped}\b", body)
    )


def builds(tree: Path, task: dict) -> bool:
    """Whether ``tree`` still compiles, checked in a throwaway copy so build
    output never lands in the diff."""
    command = (task.get("build") or {}).get("command")
    if not command:
        return True
    check = WORKDIR / "buildcheck"
    if check.exists():
        shutil.rmtree(check)
    shutil.copytree(tree, check)
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", command], cwd=check, capture_output=True, timeout=1800
    )
    shutil.rmtree(check, ignore_errors=True)
    return result.returncode == 0


def unified_diff(original: Path, patched: Path) -> str:
    """A unified diff of ``patched`` against ``original`` in the ``a/`` ``b/`` form
    a patch tool expects."""
    proc = subprocess.run(  # noqa: S603
        ["diff", "-ruN", "-x", "build", "-x", ".git", str(original), str(patched)],
        capture_output=True,
        text=True,
    )
    out: list[str] = []
    for line in proc.stdout.splitlines(keepends=True):
        if line.startswith("--- "):
            out.append("--- a/" + _relative(line[4:], original))
        elif line.startswith("+++ "):
            out.append("+++ b/" + _relative(line[4:], patched))
        else:
            out.append(line)
    return "".join(out)


def _relative(fragment: str, root: Path) -> str:
    path = fragment.split("\t")[0].strip()
    try:
        return str(Path(path).relative_to(root)) + "\n"
    except ValueError:
        return path + "\n"


def main() -> int:
    task = json.loads(TASK.read_text())
    WORKDIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    staging = WORKDIR / "patched"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(REPO, staging)

    changed: list[str] = []
    for path in sorted(staging.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        # Editing a test or benchmark fails the immutable-tree gate before anything
        # is measured, so leave those trees alone.
        if path.relative_to(staging).parts[0] in {"tests", "test", "bench"}:
            continue
        original = path.read_text(errors="replace")
        rewritten = original
        for transform in (hoist_loop_invariant_strlen, avoid_repeated_realloc):
            out = transform(rewritten)
            if out:
                rewritten = out
        if rewritten != original:
            path.write_text(rewritten)
            changed.append(str(path.relative_to(staging)))

    patch = ""
    note = "no applicable transform on this task"
    if changed and builds(staging, task):
        patch = unified_diff(REPO, staging)
        note = f"applied a behaviour-preserving speedup in {', '.join(changed)}"
    elif changed:
        note = "the transform broke the build; returning no patch"

    report = {
        "interface_version": task.get("interface_version", "cf/1"),
        "task_id": task["task_id"],
        "agent_version": "reference-1.0.0",
        "changed_files": changed if patch else [],
        "notes": f"{note}. The validator's measurement is authoritative.",
    }
    (OUTPUT / "patch.diff").write_text(patch)
    (OUTPUT / "report.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
