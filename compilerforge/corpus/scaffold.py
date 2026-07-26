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

#: An entry point makes a source file runnable on its own, which is what lets a
#: project with no build system still be built and measured.
_MAIN = re.compile(r"^\s*(?:int|void)\s+main\s*\(", re.MULTILINE)

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
    #: Set when the project has no build system and one was synthesised for it.
    entry_point: Path | None = None
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
            "entry_point": (
                str(self.entry_point.relative_to(self.root)) if self.entry_point else None
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


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


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

    # No build system. If something here has an entry point, the project is a
    # standalone program and compiling it is well defined — so a build is
    # synthesised rather than declaring defeat. The generated definition is
    # validator-owned: it is written outside the patch scope so a candidate
    # cannot change the flags it is measured under.
    entry_points = [p for p in result.source_files if _MAIN.search(_read(p))]
    if entry_points:
        result.build_system = "synthesised"
        result.entry_point = entry_points[0]
        result.build_command = (
            "cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && "
            "cmake --build build -j$(nproc)"
        )
        if len(entry_points) > 1:
            others = ", ".join(sorted(p.name for p in entry_points[1:]))
            result.warnings.append(
                f"{len(entry_points)} source files define main(); "
                f"{entry_points[0].name} was used as the entry point and the "
                f"rest were left out of the build ({others}). Two mains cannot "
                "link into one program. If the wrong one was chosen, upload "
                "only the code you want measured."
            )
        return

    result.build_system = None
    result.missing.append(
        "build_command — no CMakeLists.txt, meson.build, Makefile or configure "
        "was found, and no source file defines main(), so there is nothing to "
        "build and nothing to run"
    )


def _detect_benchmark(
    result: Detected, files: list[Path], targets: CMakeTargets | None
) -> None:
    """Find the benchmark, and decide how it can be measured.

    The measurement mode is the important half. A benchmark carrying the markers
    is measured between them; one without them is measured whole, and that is
    reported because it changes what the number means.
    """
    if result.build_system == "synthesised" and result.entry_point is not None:
        # The program is its own benchmark: there is nothing else to run.
        result.benchmark_source = result.entry_point
        result.benchmark_command = "./build/program"
        result.measured_region = (
            MARKED_REGION if _MARKER.search(_read(result.entry_point)) else WHOLE_PROCESS
        )
        result.warnings.append(
            "this project is a single program, so the code being measured and the "
            "code being changed are the same file. A candidate that computes less "
            "and still prints the same bytes is indistinguishable from one that "
            "computes the same thing faster — give the benchmark arguments that "
            "vary its work, or split the hot code into its own file, before "
            "trusting a large speedup here."
        )
        return

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
    if result.build_system == "synthesised":
        # Everything the user uploaded is theirs to change. The build definition
        # is generated by us and is deliberately not listed, so it stays
        # validator-owned and a candidate cannot alter its own compile flags.
        result.patchable_paths = tuple(
            str(p.relative_to(result.root)) for p in result.source_files
        ) + tuple(str(p.relative_to(result.root)) for p in result.header_files)
        return

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


def write_build_definition(repo: Path, detected: Detected) -> Path:
    """Generate a CMakeLists.txt for a project that has none.

    Deliberately minimal, and deliberately not listed in patchable_paths: the
    validator owns the flags a candidate is measured under. A build definition a
    candidate could edit is a candidate that can change the terms of its own
    measurement rather than the work the program does.
    """
    entry = detected.entry_point
    if entry is None:
        raise ScaffoldError("no entry point to build")

    # Every translation unit except the rival entry points. A project with a
    # benchmark and a test runner has several files defining main(), and linking
    # them into one executable fails with "multiple definition of `main`" —
    # a generated build that cannot possibly link, reported to the user as
    # "baseline does not build" with nothing pointing at the cause.
    rivals = {
        p.resolve()
        for p in detected.source_files
        if p.resolve() != entry.resolve() and _MAIN.search(_read(p))
    }
    sources = sorted(
        {
            str(p.relative_to(detected.root))
            for p in detected.source_files
            if p.resolve() not in rivals
        }
    )
    cxx = any(p.suffix.lower() != ".c" for p in detected.source_files)
    languages = "CXX" if cxx else "C"

    body = [
        "# Generated by cf-eval onboard. This project declared no build system.",
        "#",
        "# Validator-owned: it is not in the patch scope, because a candidate that",
        "# can edit its own compile flags is changing the terms of the measurement",
        "# rather than the work the program does.",
        "cmake_minimum_required(VERSION 3.16)",
        f"project(cf_generated {languages})",
        "",
    ]
    if cxx:
        body += ["set(CMAKE_CXX_STANDARD 17)", "set(CMAKE_CXX_STANDARD_REQUIRED ON)"]
    else:
        body += ["set(CMAKE_C_STANDARD 11)", "set(CMAKE_C_STANDARD_REQUIRED ON)"]
    body += [
        "",
        "# Flags arrive from the validator, which owns the optimization level and",
        "# the sanitizer selection.",
        "if(DEFINED ENV{CFLAGS})",
        '  set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")',
        "endif()",
        "if(DEFINED ENV{CXXFLAGS})",
        '  set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} $ENV{CXXFLAGS}")',
        "endif()",
        "if(DEFINED ENV{LDFLAGS})",
        '  set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")',
        "endif()",
        "",
        "add_executable(program " + " ".join(sources) + ")",
    ]
    include_dirs = sorted(
        {str(p.parent.relative_to(detected.root)) for p in detected.header_files}
    )
    for directory in include_dirs:
        if directory != ".":
            body.append(f"target_include_directories(program PRIVATE {directory})")

    target = repo / "CMakeLists.txt"
    target.write_text("\n".join(body) + "\n")
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

    if detected.build_system == "synthesised" and detected.entry_point is not None:
        write_build_definition(destination / "repo", detected)

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
