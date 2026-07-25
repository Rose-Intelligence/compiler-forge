"""The reference optimization agent shipped in the miner SDK.

Its job is not to win. It is to be one rung of the published reference ladder:

    "The reference optimization agent shipped in the miner SDK — the 'is the miner
     beating the freely available starting point?' control."

A leaderboard that shows only miner scores is a marketing artifact. One that shows
miner scores alongside -O3, PGO, a naive LLM patcher and *this* is a research
instrument. That is the whole reason this file exists.

It also demonstrates the loop the network actually rewards:
reproduce the baseline, prioritise bottlenecks against the declared objective,
generate multiple candidates rather than stopping at the first plausible edit,
verify locally, discard anything that changes behaviour, and return the best
survivor — or return nothing, because an honest empty result is scored above a
rejected one.

The candidate generators here are deliberately simple and deterministic. A
competitive harness would drive candidate generation with the validator-supplied
model through the metered proxy; ``inference.py`` provides that client, and
``propose_with_model`` shows where it plugs in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

TASK_PATH = Path(os.getenv("CF_LOCAL_TASK", "/task")) / "task.json"
REPO_PATH = Path(os.getenv("CF_LOCAL_REPO", "/workspace/repo"))
OUTPUT_PATH = Path(os.getenv("CF_LOCAL_OUTPUT", "/output"))


@dataclass(slots=True)
class Candidate:
    """One proposed transformation, before anything is known about whether it works."""

    name: str
    strategy: str
    files: dict[str, str]  # relpath -> new content
    rationale: str


@dataclass(slots=True)
class CandidateOutcome:
    candidate: Candidate
    accepted: bool
    reason: str
    speedup: float | None = None


@dataclass(slots=True)
class Hotspot:
    """A function worth looking at, and why."""

    function: str
    source_file: str | None
    instruction_share: float

    def __str__(self) -> str:
        return f"{self.function} ({self.instruction_share:.1%} of instructions)"


class ReferenceAgent:
    """Profile, propose, verify, roll back."""

    def __init__(self, task: dict, repo: Path, workdir: Path) -> None:
        self.task = task
        self.repo = repo
        self.workdir = workdir
        self.objective = task.get("benchmark", {}).get("objective", "balanced")
        self.seed = task.get("seed", "0x0")
        self.rejected: dict[str, str] = {}
        self.candidates_tried = 0
        self.started = time.monotonic()

    # -- step 1: reproduce the baseline ----------------------------------

    def build(self, tree: Path) -> bool:
        """Build with the compiler the task contract pins.

        Setting CC matters as much here as it does on the validator. Without it
        the build system picks whatever it finds first, and the agent ends up
        optimizing against a different compiler than the one it is measured
        under — tuning one program and being scored on another.
        """
        build = self.task["build"]
        env = {
            **os.environ,
            "CC": build.get("c_compiler", "clang"),
            "CXX": build.get("cxx_compiler", "clang++"),
        }
        proc = subprocess.run(  # noqa: S603
            ["/bin/sh", "-c", build["command"]],
            cwd=tree,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        return proc.returncode == 0

    def benchmark_output(self, tree: Path) -> bytes | None:
        """The observable the agent uses for its own equivalence check.

        Weaker than the validator's differential stage by design — the agent
        cannot see the hidden inputs. It exists to discard obviously broken
        candidates cheaply, before spending measurement budget on them.
        """
        proc = subprocess.run(  # noqa: S603
            ["/bin/sh", "-c", self.task["benchmark"]["command"]],
            cwd=tree,
            capture_output=True,
            timeout=self.task["benchmark"].get("max_wall_seconds", 1800),
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else None

    def measure(self, tree: Path) -> int | None:
        """Instruction count via Callgrind, matching the validator's Tier A.

        A harness that optimizes against wall-clock on a shared machine is
        optimizing against noise. Using the same deterministic counter the
        validator uses is the single highest-value thing a miner can do.
        """
        return _callgrind_instructions(
            self.task["benchmark"]["command"], cwd=tree, workdir=self.workdir
        )

    # -- step 2: prioritise bottlenecks ----------------------------------

    def profile(self, tree: Path) -> list[Hotspot]:
        """Rank functions by instruction share.

        Prioritising against the declared objective rather than against whatever
        is easiest to change is explicitly part of the agent contract.
        """
        text = _callgrind_run(
            self.task["benchmark"]["command"], cwd=tree, workdir=self.workdir
        )
        return _parse_hotspots(text) if text else []

    # -- step 3: generate candidates -------------------------------------

    def propose(self, tree: Path, hotspots: list[Hotspot]) -> list[Candidate]:
        """Generate multiple candidates rather than stopping at the first edit."""
        sources = _source_files(tree, hotspots)
        candidates: list[Candidate] = []
        for transform in (
            _hoist_loop_invariant_strlen,
            _replace_pow_with_multiplication,
            _strength_reduce_division_by_constant,
            _avoid_repeated_realloc,
        ):
            for rel, path in sources:
                original = path.read_text(errors="replace")
                changed = transform(original)
                if changed is None or changed == original:
                    continue
                candidates.append(
                    Candidate(
                        name=f"{transform.__name__}:{rel}",
                        strategy=transform.__doc__.strip().splitlines()[0]
                        if transform.__doc__
                        else transform.__name__,
                        files={rel: changed},
                        rationale=f"applied to {rel}, which appears in the profile",
                    )
                )
        return candidates

    def propose_with_model(self, tree: Path, hotspots: list[Hotspot]) -> list[Candidate]:
        """Where a competitive harness would drive candidate generation with the
        validator-supplied model through the metered proxy.

        Left as a documented extension point rather than a half-implementation:
        the reference agent is a control, and a control whose behaviour depends on
        an external service is not a control.
        """
        from compilerforge.miner.reference_agent.inference import InferenceClient

        client = InferenceClient.from_environment()
        if client is None:
            return []
        return client.propose_candidates(tree, hotspots, self.task)

    # -- step 4: verify and roll back ------------------------------------

    def evaluate_candidate(
        self, candidate: Candidate, baseline_output: bytes, baseline_cost: int | None
    ) -> CandidateOutcome:
        """Apply, build, check behaviour, measure. Discard on any doubt."""
        self.candidates_tried += 1
        tree = self.workdir / f"cand-{self.candidates_tried}"
        if tree.exists():
            shutil.rmtree(tree)
        shutil.copytree(self.repo, tree)

        for rel, content in candidate.files.items():
            (tree / rel).write_text(content)

        if not self.build(tree):
            shutil.rmtree(tree, ignore_errors=True)
            return CandidateOutcome(candidate, False, "build failed")

        output = self.benchmark_output(tree)
        if output is None:
            shutil.rmtree(tree, ignore_errors=True)
            return CandidateOutcome(candidate, False, "benchmark did not complete")
        if output != baseline_output:
            # Reject candidates that alter required behaviour. The
            # validator's differential stage is stricter; failing our own weaker
            # check means it would certainly fail theirs.
            shutil.rmtree(tree, ignore_errors=True)
            return CandidateOutcome(candidate, False, "differential mismatch")

        cost = self.measure(tree)
        shutil.rmtree(tree, ignore_errors=True)
        if cost is None or baseline_cost is None:
            return CandidateOutcome(candidate, False, "could not measure deterministically")

        speedup = baseline_cost / cost if cost else 0.0
        if speedup <= 1.0:
            return CandidateOutcome(candidate, False, f"no improvement ({speedup:.4f}x)", speedup)
        return CandidateOutcome(candidate, True, f"{speedup:.4f}x", speedup)

    # -- the loop --------------------------------------------------------

    def run(self) -> tuple[str, dict]:
        """Returns ``(patch, report)``."""
        baseline_tree = self.workdir / "baseline"
        if baseline_tree.exists():
            shutil.rmtree(baseline_tree)
        shutil.copytree(self.repo, baseline_tree)

        if not self.build(baseline_tree):
            return "", self._report(None, [], note="baseline does not build; returning no patch")

        baseline_output = self.benchmark_output(baseline_tree)
        if baseline_output is None:
            return "", self._report(None, [], note="baseline benchmark failed; returning no patch")

        baseline_cost = self.measure(baseline_tree)
        hotspots = self.profile(baseline_tree)

        candidates = self.propose(baseline_tree, hotspots)
        outcomes = [
            self.evaluate_candidate(c, baseline_output, baseline_cost) for c in candidates
        ]

        accepted = [o for o in outcomes if o.accepted]
        for i, o in enumerate(outcomes):
            if not o.accepted:
                self.rejected[str(i)] = o.reason

        if not accepted:
            # An honest empty result, scored above a rejected one.
            return "", self._report(None, hotspots, note="no candidate survived verification")

        best = max(accepted, key=lambda o: o.speedup or 0.0)
        patch = self._make_patch(best.candidate)
        return patch, self._report(best, hotspots)

    def _make_patch(self, candidate: Candidate) -> str:
        """Produce a unified diff against the pinned revision."""
        staging = self.workdir / "patched"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(self.repo, staging)
        for rel, content in candidate.files.items():
            (staging / rel).write_text(content)

        # No --label here: for a recursive diff it would stamp the same label on
        # every file, collapsing a multi-file patch into one unusable path.
        # The real paths are rewritten into a/ b/ form afterwards instead.
        proc = subprocess.run(  # noqa: S603
            ["diff", "-ruN", str(self.repo), str(staging)],
            capture_output=True,
            text=True,
            check=False,
        )
        return _rewrite_diff_paths(proc.stdout, self.repo, staging)

    def _report(
        self, best: CandidateOutcome | None, hotspots: list[Hotspot], *, note: str = ""
    ) -> dict:
        return {
            "interface_version": self.task.get("interface_version", "cf/1"),
            "task_id": self.task["task_id"],
            "agent_version": "reference-1.0.0",
            "objective": self.objective,
            "changed_files": sorted(best.candidate.files) if best else [],
            "claimed_strategy": [best.candidate.strategy] if best else [],
            "candidate_count": self.candidates_tried,
            "selected_candidate": None,
            "rejected_reasons": self.rejected,
            "self_measurement": {
                "local_speedup_estimate": best.speedup if best else None,
                "method": "callgrind",
            },
            "budget_used": {
                "wall_seconds": round(time.monotonic() - self.started, 2),
                "model_tokens": 0,
            },
            "notes": note
            or f"hotspots: {', '.join(str(h) for h in hotspots[:3])}. "
            "Informational only. Validator measurements are authoritative.",
        }



def _callgrind_run(command: str, *, cwd: Path, workdir: Path) -> str | None:
    """Run one Callgrind measurement and return the profile with real counts.

    Two flags here are not optional, and getting either wrong silently reports
    that the program did no work at all:

    ``--trace-children=yes`` — the benchmark is launched through a shell, and
    without this Valgrind profiles only the shell, which never enters the
    instrumented region.

    ``%p`` in the output filename — with child tracing on, every traced process
    writes to the path given, so a fixed name lets the last process to exit
    (usually the uninstrumented shell) clobber the benchmark's profile.
    """
    if not shutil.which("valgrind"):
        return None

    out_dir = workdir / "callgrind"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(  # noqa: S603
        [
            "valgrind",
            "--tool=callgrind",
            f"--callgrind-out-file={out_dir}/out.%p",
            "--instr-atstart=no",
            "--cache-sim=yes",
            "--trace-children=yes",
            "--",
            "/bin/sh",
            "-c",
            command,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if proc.returncode != 0:
        return None

    # Keep the profile that actually recorded instructions; the shell's is empty.
    best_text, best_count = None, 0
    for path in sorted(out_dir.glob("out.*")):
        text = path.read_text(errors="replace")
        count = _total_instructions(text) or 0
        if count > best_count:
            best_text, best_count = text, count
    return best_text


def _callgrind_instructions(command: str, *, cwd: Path, workdir: Path) -> int | None:
    text = _callgrind_run(command, cwd=cwd, workdir=workdir)
    return _total_instructions(text) if text else None


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------
#
# Each returns the transformed source, or None when it does not apply. They are
# intentionally conservative: a transformation that is only *usually* safe belongs
# in a competitive harness with its own verification budget, not in the control.


def _hoist_loop_invariant_strlen(source: str) -> str | None:
    """Hoist a loop-invariant strlen out of a for-loop condition.

    ``for (i = 0; i < strlen(s); i++)`` re-walks the string on every iteration,
    making a linear scan quadratic. The classic case of a correct program doing
    far more work than the job requires.

    The length is bound immediately before the loop, at the loop's own
    indentation, so the result is ordinary readable C rather than a scoping
    trick. Only applied when the loop body cannot modify the string — see below.
    """
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)for\s*\(\s*(?P<init>[^;]*?)\s*;\s*"
        r"(?P<var>\w+)\s*<\s*strlen\s*\(\s*(?P<arg>\w+)\s*\)\s*;"
        r"(?P<rest>[^\n]*)$",
        re.MULTILINE,
    )

    def replace(m: re.Match[str]) -> str:
        indent, arg, var = m.group("indent"), m.group("arg"), m.group("var")
        length_var = f"cf_len_{arg}"
        return (
            f"{indent}const size_t {length_var} = strlen({arg});\n"
            f"{indent}for ({m.group('init')}; {var} < {length_var};{m.group('rest')}"
        )

    # Hoisting is only sound while the loop body leaves the string alone. Check
    # the body specifically: scanning the whole file would decline any file that
    # writes through a same-named pointer anywhere, which in practice means
    # declining everything.
    for match in pattern.finditer(source):
        body = _loop_body(source, match.end())
        if body is None or _writes_through(body, match.group("arg")):
            return None

    changed, n = pattern.subn(replace, source)
    return changed if n else None


def _loop_body(source: str, from_index: int) -> str | None:
    """Return the body of the loop whose header ends at ``from_index``.

    The header match consumes the rest of its line, including the opening brace
    when the body is braced. Searching forward for the next ``{`` therefore found
    the *following* block — a different function — and the write analysis was run
    against code that had nothing to do with the loop. An unbraced loop that
    truncated its string was hoisted as safe on the strength of an unrelated
    function containing no writes.

    So the brace is looked for only on the header's own line. A loop whose body
    is a single unbraced statement is returned as that statement, and one whose
    body cannot be delimited at all returns None so the caller declines.
    """
    line_end = source.find("\n", from_index)
    if line_end < 0:
        line_end = len(source)
    header_tail = source[from_index:line_end]

    if "{" in header_tail:
        open_index = from_index + header_tail.index("{")
    elif from_index > 0 and source[from_index - 1] == "{":
        # The regex ended exactly on the brace.
        open_index = from_index - 1
    else:
        # Unbraced: the body is the next statement. Everything up to the first
        # semicolon at nesting depth zero.
        depth = 0
        for i in range(from_index, len(source)):
            char = source[i]
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == "{":
                # A brace on a later line still belongs to this loop.
                open_index = i
                break
            elif char == ";" and depth <= 0:
                return source[from_index : i + 1]
        else:
            return None
    if open_index < 0:
        return None
    depth = 0
    for i in range(open_index, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_index : i + 1]
    return None


def _writes_through(body: str, name: str) -> bool:
    """Whether ``body`` assigns through ``name``, which would move its length."""
    escaped = re.escape(name)
    return bool(
        re.search(rf"\b{escaped}\s*\[[^\]]*\]\s*=[^=]", body)
        or re.search(rf"\*\s*{escaped}\s*=[^=]", body)
        or re.search(rf"\b{escaped}\s*=[^=]", body)
    )


def _replace_pow_with_multiplication(source: str) -> str | None:
    """Replace pow(x, 2) and pow(x, 3) with multiplication.

    A libm call in a hot loop for something the compiler cannot always constant-fold.
    """
    changed = source
    changed = re.sub(r"\bpow\s*\(\s*([A-Za-z_]\w*)\s*,\s*2(?:\.0*)?\s*\)", r"((\1) * (\1))", changed)
    changed = re.sub(
        r"\bpow\s*\(\s*([A-Za-z_]\w*)\s*,\s*3(?:\.0*)?\s*\)", r"((\1) * (\1) * (\1))", changed
    )
    return changed if changed != source else None


def _strength_reduce_division_by_constant(source: str) -> str | None:
    """Turn division by a power of two into a shift for unsigned values.

    Only applied where the declared type is explicitly unsigned, because the
    transformation is wrong for signed negatives — exactly the kind of "fast but
    unsound" edit UBSan exists to catch.
    """
    pattern = re.compile(
        r"\b(?P<decl>unsigned\s+(?:int|long)|size_t|uint32_t|uint64_t)\s+(?P<var>\w+)\b"
    )
    unsigned_vars = {m.group("var") for m in pattern.finditer(source)}
    if not unsigned_vars:
        return None

    def replace(m: re.Match[str]) -> str:
        var, divisor = m.group(1), int(m.group(2))
        if var not in unsigned_vars or divisor <= 0 or divisor & (divisor - 1):
            return m.group(0)
        return f"({var} >> {divisor.bit_length() - 1})"

    changed, n = re.subn(r"\b(\w+)\s*/\s*(\d+)\b", replace, source)
    return changed if n and changed != source else None


def _avoid_repeated_realloc(source: str) -> str | None:
    """Grow a buffer geometrically instead of by one element at a time.

    ``realloc(p, ++n * sizeof(T))`` inside a loop is quadratic in copies.
    """
    pattern = re.compile(
        r"realloc\s*\(\s*(?P<ptr>\w+)\s*,\s*\+\+\s*(?P<count>\w+)\s*\*\s*sizeof"
    )
    if not pattern.search(source):
        return None
    return pattern.sub(
        lambda m: (
            f"realloc({m.group('ptr')}, "
            f"(++{m.group('count')} < 8 ? 8 : {m.group('count')} * 2) * sizeof"
        ),
        source,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _source_files(tree: Path, hotspots: list[Hotspot]) -> list[tuple[str, Path]]:
    """Source files worth editing, hot ones first."""
    hot_names = {Path(h.source_file).name for h in hotspots if h.source_file}
    all_sources = [
        (str(p.relative_to(tree)), p)
        for p in sorted(tree.rglob("*"))
        if p.suffix in {".c", ".cpp", ".cc", ".cxx"} and p.is_file()
    ]
    hot = [(rel, p) for rel, p in all_sources if p.name in hot_names]
    cold = [(rel, p) for rel, p in all_sources if p.name not in hot_names]
    return hot + cold


_COST_LINE_RE = re.compile(r"^(?:summary|totals):\s*(.+)$", re.MULTILINE)
_FN_RE = re.compile(r"^fn=\(\d+\)\s*(.+)$", re.MULTILINE)
_FL_RE = re.compile(r"^fl=\(\d+\)\s*(.+)$", re.MULTILINE)


def _total_instructions(text: str) -> int | None:
    """Instruction count from a callgrind.out file.

    Callgrind emits both a `summary:` and a `totals:` cost line, and which one
    carries the bracketed region depends on the Valgrind version. Take the
    largest non-zero first field rather than assuming either.
    """
    best: int | None = None
    for raw in _COST_LINE_RE.findall(text):
        parts = raw.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if value > 0 and (best is None or value > best):
            best = value
    return best


def _parse_hotspots(text: str) -> list[Hotspot]:
    """Attribute instruction counts to functions from a callgrind.out file."""
    total = _total_instructions(text) or 0
    if not total:
        return []

    costs: dict[str, int] = {}
    files: dict[str, str] = {}
    current_fn: str | None = None
    current_fl: str | None = None

    for line in text.splitlines():
        if line.startswith("fl="):
            m = _FL_RE.match(line)
            if m:
                current_fl = m.group(1)
        elif line.startswith("fn="):
            m = _FN_RE.match(line)
            if m:
                current_fn = m.group(1)
                if current_fl:
                    files[current_fn] = current_fl
        elif current_fn and line and line[0].isdigit():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    costs[current_fn] = costs.get(current_fn, 0) + int(parts[1])
                except ValueError:
                    continue

    return [
        Hotspot(function=fn, source_file=files.get(fn), instruction_share=cost / total)
        for fn, cost in sorted(costs.items(), key=lambda kv: -kv[1])[:20]
    ]


def _rewrite_diff_paths(diff: str, original: Path, patched: Path) -> str:
    """Rewrite absolute diff paths into the a/ b/ form ``git apply`` expects."""
    out: list[str] = []
    for line in diff.splitlines(keepends=True):
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
    """Entry point. Reads /task/task.json, writes /output/{patch.diff,report.json}."""
    task = json.loads(TASK_PATH.read_text())
    workdir = Path(os.getenv("CF_AGENT_WORKDIR", "/tmp/cf-agent"))
    workdir.mkdir(parents=True, exist_ok=True)

    agent = ReferenceAgent(task=task, repo=REPO_PATH, workdir=workdir)
    patch, report = agent.run()

    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    (OUTPUT_PATH / "patch.diff").write_text(patch)
    (OUTPUT_PATH / "report.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
