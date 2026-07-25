"""Turn a source tree into a task package.

Onboarding a repository has always been the expensive step: someone had to hand
write a manifest, and until they did, the pipeline could not touch their code.
This does the mechanical part — detect the build system, locate the tests and the
benchmark, decide which measurement mode applies — and states plainly what it
could not work out.

Two rules shape everything here.

**It never guesses a command it cannot see.** A wrong build command produces a
confusing failure three stages later; an absent one produces a clear error now.
So detection either finds evidence in the tree or reports the field as missing.

**It never silently downgrades the measurement.** A benchmark carrying the
``cf_bench_start``/``cf_bench_stop`` markers is measured between them. One
without them is measured whole — which is correct but blunter, because the
improvement is diluted by process startup. That downgrade is reported, not
absorbed, because a user comparing a 1.4x here against a 3.9x on the curated
corpus deserves to know the two numbers were not taken the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from compilerforge.protocol.task import MARKED_REGION, WHOLE_PROCESS

#: Directories that are never part of a source tree worth scanning.
_SKIP = {
    ".git", ".hg", ".svn", "node_modules", "build", "cmake-build-debug",
    "cmake-build-release", "__pycache__", ".venv", "venv", "dist", "target",
    ".cache", "third_party", "vendor", "external", "deps", "subprojects",
}

_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".c++"}
_HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".h++"}

_MARKER = re.compile(r"CF_BENCH_START|cf_bench_start|CALLGRIND_START_INSTRUMENTATION")

#: CMake gives us the answer directly, so we read it instead of guessing from
#: names. A project whose tests live in `unit/` rather than `tests/` is normal,
#: and a heuristic that misses them does not merely fail to find the suite — it
#: leaves those files patchable, which would let a candidate delete the tests it
#: is being judged by.
_ADD_EXECUTABLE = re.compile(
    r"add_executable\s*\(\s*([A-Za-z0-9_.-]+)([^)]*)\)", re.IGNORECASE | re.DOTALL)
_ADD_TEST = re.compile(
    r"add_test\s*\((?P<body>[^)]*)\)", re.IGNORECASE | re.DOTALL)
_COMMAND_TARGET = re.compile(r"COMMAND\s+([A-Za-z0-9_.-]+)", re.IGNORECASE)


@dataclass
class CMakeTargets:
    """Executable targets and their sources, read from the build definition."""

    sources: dict[str, list[str]] = field(default_factory=dict)
    test_targets: set[str] = field(default_factory=set)

    def harness_files(self) -> set[str]:
        files: set[str] = set()
        for target in self.test_targets:
            files.update(self.sources.get(target, []))
        return files


def _parse_cmake(root: Path) -> CMakeTargets | None:
    """Read executable targets and registered tests out of CMakeLists.txt."""
    found = CMakeTargets()
    listfiles = [root / "CMakeLists.txt"]
    listfiles += [p for p in root.rglob("CMakeLists.txt") if p != listfiles[0]]
    listfiles = [p for p in listfiles if p.is_file() and not any(x in _SKIP for x in p.parts)]
    if not listfiles:
        return None

    for listfile in listfiles:
        try:
            text = listfile.read_text(errors="replace")
        except OSError:
            continue
        prefix = listfile.parent.relative_to(root)

        for match in _ADD_EXECUTABLE.finditer(text):
            target = match.group(1)
            if target.upper() in {"WIN32", "MACOSX_BUNDLE", "EXCLUDE_FROM_ALL"}:
                continue
            sources = [
                str(prefix / token) if str(prefix) != "." else token
                for token in match.group(2).split()
                if Path(token).suffix.lower() in _SOURCE_SUFFIXES | _HEADER_SUFFIXES
            ]
            found.sources.setdefault(target, []).extend(sources)

        for match in _ADD_TEST.finditer(text):
            for target in _COMMAND_TARGET.findall(match.group("body")):
                found.test_targets.add(target)

    return found if found.sources or found.test_targets else None

#: Names that look like a benchmark, most specific first.
_BENCH_HINTS = ("bench", "benchmark", "perf")
_TEST_HINTS = ("test", "tests", "check", "spec")


class ScaffoldError(RuntimeError):
    """The tree cannot be turned into a package without more information."""


@dataclass
class Detected:
    """What the tree told us, and what it did not."""

    root: Path
    build_system: str | None = None
    build_command: str | None = None
    test_command: str | None = None
    benchmark_command: str | None = None
    benchmark_source: Path | None = None
    measured_region: str = WHOLE_PROCESS
    source_files: list[Path] = field(default_factory=list)
    header_files: list[Path] = field(default_factory=list)
    patchable_paths: tuple[str, ...] = ("src/**", "include/**")
    languages: tuple[str, ...] = ()
    #: Things the user must supply or confirm. Empty means ready to run.
    missing: list[str] = field(default_factory=list)
    #: Things that are fine but change how the result should be read.
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict:
        return {
            "build_system": self.build_system,
            "build_command": self.build_command,
            "test_command": self.test_command,
            "benchmark_command": self.benchmark_command,
            "benchmark_source": (
                str(self.benchmark_source.relative_to(self.root))
                if self.benchmark_source
                else None
            ),
            "measured_region": self.measured_region,
            "instrumented": self.measured_region == MARKED_REGION,
            "source_files": len(self.source_files),
            "header_files": len(self.header_files),
            "patchable_paths": list(self.patchable_paths),
            "languages": list(self.languages),
            "missing": list(self.missing),
            "warnings": list(self.warnings),
            "ready": self.ready,
        }


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if any(part in _SKIP for part in path.parts):
            continue
        if path.is_file():
            found.append(path)
    return found


def _looks_like(path: Path, hints: tuple[str, ...]) -> bool:
    lowered = str(path).lower()
    return any(h in lowered for h in hints)


def detect(root: Path) -> Detected:
    """Inspect a source tree and report what can be measured."""
    root = root.resolve()
    if not root.is_dir():
        raise ScaffoldError(f"{root} is not a directory")

    result = Detected(root=root)
    files = _walk(root)
    if not files:
        raise ScaffoldError("the uploaded project contains no files")

    result.source_files = sorted(
        p for p in files if p.suffix.lower() in _SOURCE_SUFFIXES
    )
    result.header_files = sorted(
        p for p in files if p.suffix.lower() in _HEADER_SUFFIXES
    )

    if not result.source_files:
        raise ScaffoldError(
            "no C or C++ source files found. This pipeline measures compiled "
            "native code; interpreted and JIT-compiled languages cannot be "
            "measured deterministically by instruction count."
        )

    languages = set()
    for p in result.source_files:
        languages.add("C" if p.suffix.lower() == ".c" else "C++")
    result.languages = tuple(sorted(languages))

    _detect_build(result, files)
    targets = _parse_cmake(root) if result.build_system == "cmake" else None
    _detect_benchmark(result, files, targets)
    _detect_tests(result, files, targets)
    _detect_patch_scope(result, targets)

    return result


def _detect_build(result: Detected, files: list[Path]) -> None:
    names = {p.name for p in files if p.parent == result.root}

    if "CMakeLists.txt" in names:
        result.build_system = "cmake"
        result.build_command = (
            "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && "
            "cmake --build build -j$(nproc)"
        )
        return
    if "meson.build" in names:
        result.build_system = "meson"
        result.build_command = "meson setup build --buildtype=release && meson compile -C build"
        return
    if "Makefile" in names or "makefile" in names:
        result.build_system = "make"
        result.build_command = "make -j$(nproc)"
        return
    if "configure" in names:
        result.build_system = "autotools"
        result.build_command = "./configure && make -j$(nproc)"
        return

    result.build_system = None
    result.missing.append(
        "build_command — no CMakeLists.txt, meson.build, Makefile or configure "
        "was found at the top level, so the build cannot be inferred"
    )


def _detect_benchmark(
    result: Detected, files: list[Path], targets: CMakeTargets | None
) -> None:
    """Find the benchmark, and decide how it can be measured.

    The measurement mode is the important half. A benchmark carrying the markers
    is measured between them; one without them is measured whole, and that is
    reported because it changes what the number means.
    """
    candidates: list[Path] = []
    if targets is not None:
        # An executable that is not registered as a test is the benchmark. This
        # is evidence from the build definition rather than a guess about names.
        for target, sources in targets.sources.items():
            if target in targets.test_targets:
                continue
            for source in sources:
                path = result.root / source
                if path.is_file():
                    candidates.append(path)
    if not candidates:
        candidates = [
            p for p in result.source_files
            if _looks_like(p.relative_to(result.root), _BENCH_HINTS)
        ]
    if candidates:
        # Prefer a file whose own name is the hint over one that merely sits in a
        # directory that matches.
        candidates.sort(key=lambda p: (not _looks_like(Path(p.name), _BENCH_HINTS), len(str(p))))
        result.benchmark_source = candidates[0]

        try:
            text = candidates[0].read_text(errors="replace")
        except OSError:
            text = ""
        if _MARKER.search(text):
            result.measured_region = MARKED_REGION
        else:
            result.measured_region = WHOLE_PROCESS
            result.warnings.append(
                f"{candidates[0].relative_to(result.root)} has no "
                "cf_bench_start/cf_bench_stop markers, so the whole process is "
                "measured including startup. The speedup is real but understated: "
                "add the markers around the hot region for a sharper number."
            )
    else:
        result.measured_region = WHOLE_PROCESS
        result.warnings.append(
            "no file looking like a benchmark was found, so the whole process is "
            "measured. Point benchmark_command at whatever exercises the code you "
            "care about."
        )

    if result.build_system == "cmake" and result.benchmark_source is not None:
        stem = result.benchmark_source.stem
        result.benchmark_command = f"./build/{stem}"
    elif result.benchmark_source is not None:
        result.benchmark_command = f"./{result.benchmark_source.stem}"

    if not result.benchmark_command:
        result.missing.append(
            "benchmark_command — nothing that looks like a benchmark was found. "
            "Supply the command that runs your workload; without one there is "
            "nothing to measure"
        )


def _detect_tests(
    result: Detected, files: list[Path], targets: CMakeTargets | None
) -> None:
    """Tests are not optional.

    Every gate that establishes the patch did not change behaviour runs the
    project's own suite. A project without one can still be measured, but the
    correctness evidence is far weaker and the user should be told so rather
    than handed a speedup that nothing checked.
    """
    candidates: list[Path] = []
    if targets is not None and targets.test_targets:
        candidates = [
            result.root / s
            for target in targets.test_targets
            for s in targets.sources.get(target, [])
            if (result.root / s).is_file()
        ]
    if not candidates:
        candidates = [
            p for p in result.source_files
            if _looks_like(p.relative_to(result.root), _TEST_HINTS)
        ]
    if not candidates:
        result.warnings.append(
            "no test sources were found. The pipeline will still measure the "
            "patch, but 'it still works' rests on the differential comparison "
            "alone, which is weaker than a suite the project's authors wrote."
        )
        result.test_command = "true"
        return

    if result.build_system == "cmake":
        result.test_command = "ctest --test-dir build --output-on-failure"
    elif result.build_system == "make":
        result.test_command = "make test"
    else:
        stem = candidates[0].stem
        result.test_command = f"./build/{stem}"


def _detect_patch_scope(result: Detected, targets: CMakeTargets | None) -> None:
    """Which directories a candidate may rewrite.

    Defaults to src/ and include/ when the project uses them. Otherwise every
    directory holding a source file becomes patchable except the ones holding the
    tests and the benchmark — those belong to the harness, and a candidate that
    can edit its own test is not being tested.
    """
    relative = [p.relative_to(result.root) for p in result.source_files]
    tops = {p.parts[0] for p in relative if len(p.parts) > 1}

    if "src" in tops or "include" in tops:
        scope = [f"{d}/**" for d in ("src", "include") if d in tops or d == "include"]
        result.patchable_paths = tuple(scope)
        return

    # Fail closed: anything belonging to a test or benchmark target, plus
    # anything merely *named* like one, is harness. A file wrongly kept
    # immutable costs a candidate some freedom; a test left patchable lets a
    # candidate delete the thing judging it.
    harness_files = set(targets.harness_files()) if targets else set()
    if result.benchmark_source is not None:
        harness_files.add(str(result.benchmark_source.relative_to(result.root)))

    harness = set()
    for p in relative:
        if (
            str(p) in harness_files
            or _looks_like(p, _TEST_HINTS)
            or _looks_like(p, _BENCH_HINTS)
        ):
            harness.add(p.parts[0] if len(p.parts) > 1 else p.name)

    directories = sorted(
        {p.parts[0] for p in relative if len(p.parts) > 1} - harness
    )
    if directories:
        result.patchable_paths = tuple(f"{d}/**" for d in directories)
    else:
        # Flat project: name the source files individually so the harness files
        # sitting beside them stay immutable.
        keep = [
            str(p) for p in relative
            if len(p.parts) == 1
            and not _looks_like(p, _TEST_HINTS)
            and not _looks_like(p, _BENCH_HINTS)
        ]
        if not keep:
            result.missing.append(
                "patchable_paths — every source file looks like a test or a "
                "benchmark, so there is nothing a candidate would be allowed to "
                "change"
            )
        result.patchable_paths = tuple(keep)


_TEMPLATE = Path(__file__).parent / "templates" / "cf_cases.py.in"


def write_case_generator(destination: Path, argument_sets: list[list[str]]) -> Path:
    """Write the fallback differential generator into a scaffolded package.

    Used when the project has no harness of its own: it replays the benchmark
    across the given argument sets so there is something to compare, rather than
    letting the differential gate fail for want of any input at all.
    """
    tools = destination / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    target = tools / "cf_cases.py"
    target.write_text(
        _TEMPLATE.read_text().replace("__ARGUMENT_SETS__", repr(argument_sets))
    )
    target.chmod(0o755)
    return target


def write_package(
    detected: Detected,
    destination: Path,
    *,
    package_id: str,
    family: str = "cli_utilities",
    benchmark_args: list[str] | None = None,
    differential_argument_sets: list[list[str]] | None = None,
    overrides: dict | None = None,
) -> Path:
    """Write a package.yaml beside a copy of the tree.

    The package deliberately declares no ``reference``: an uploaded project has
    no expert patch to normalise capture against, so it can be verified and
    measured but not scored. That is the honest shape, and the evaluator enforces
    it rather than inventing a denominator.
    """
    if not detected.ready:
        raise ScaffoldError("; ".join(detected.missing))

    overrides = overrides or {}
    manifest = {
        "package_id": package_id,
        "family": family,
        "license": overrides.get("license", "UNKNOWN"),
        "revision": overrides.get("revision", "0" * 40),
        "hidden_family": False,
        "build_command": overrides.get("build_command") or detected.build_command,
        "forbidden_flags": ["-ffast-math", "-fno-strict-aliasing", "-fwrapv"],
        "c_compiler": "clang",
        "cxx_compiler": "clang++",
        "patchable_paths": list(
            overrides.get("patchable_paths") or detected.patchable_paths
        ),
        "test_command": overrides.get("test_command") or detected.test_command,
        "test_inventory_globs": ["tests/**/*", "test/**/*"],
        "benchmark_command": overrides.get("benchmark_command")
        or detected.benchmark_command,
        "objective": "balanced",
        "measured_region": overrides.get("measured_region") or detected.measured_region,
        "equivalence_discipline": overrides.get("equivalence_discipline", "byte_equal"),
        "side_effects": ["stdout", "exit_code"],
        "workload_profiles": [
            {
                "name": "default",
                "args": benchmark_args or [],
                "published": True,
                "selection_weight": 1.0,
            }
        ],
        "resources": {
            "cpu_cores": 4,
            "ram_gb": 8,
            "disk_gb": 16,
            "pids_max": 512,
            "model_token_budget": 150000,
        },
        "fuzz_targets": [],
    }

    destination.mkdir(parents=True, exist_ok=True)

    # Equivalence needs inputs. A project with its own harness supplies them; one
    # without gets the benchmark replayed across a few argument sets, and the
    # warning records that this is weaker evidence rather than letting the number
    # look as solid as a curated package's.
    if overrides.get("differential_command"):
        manifest["differential_command"] = overrides["differential_command"]
    else:
        sets = differential_argument_sets or [benchmark_args or []]
        write_case_generator(destination, sets)
        manifest["input_generator"] = "python3 tools/cf_cases.py"
        detected.warnings.append(
            "no differential harness, so equivalence rests on replaying the "
            "benchmark and comparing stdout. A change the benchmark does not "
            "print is a change this cannot catch."
        )

    (destination / "package.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return destination / "package.yaml"
