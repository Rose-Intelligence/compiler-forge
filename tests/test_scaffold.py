"""Onboarding a project that declares no build system.

The synthesised build is the only thing standing between an uploaded source
tree and a measurement, and a generated build that cannot link is reported to
the user as "baseline does not build" with nothing pointing at the cause.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from compilerforge.corpus.scaffold import detect, write_build_definition

LIB_H = "long work(long n);\n"
LIB_C = '#include "lib.h"\nlong work(long n){long s=0;for(long i=0;i<n;i++)s+=i%7;return s;}\n'
BENCH_C = '#include <stdio.h>\n#include "lib.h"\nint main(void){printf("%ld\\n",work(100000));return 0;}\n'
TEST_C = '#include <assert.h>\n#include "lib.h"\nint main(void){assert(work(10)>=0);return 0;}\n'
TOOL_C = '#include <stdio.h>\n#include "lib.h"\nint main(void){printf("tool\\n");return 0;}\n'


def _tree(root, files: dict[str, str]):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_only_one_main_reaches_the_generated_executable(tmp_path):
    """The regression: several mains were linked into one program.

    A project with a benchmark, a test runner and a tool has three files
    defining main(). Listing all of them in one add_executable fails at link
    with "multiple definition of `main`", so the generated build could never
    have worked — for any such project, not just an unusual one.
    """
    repo = _tree(tmp_path / "repo", {
        "src/lib.h": LIB_H,
        "src/lib.c": LIB_C,
        "bench/bench.c": BENCH_C,
        "tests/test_lib.c": TEST_C,
        "tools/tool.c": TOOL_C,
    })

    detected = detect(repo)
    assert detected.build_system == "synthesised"
    assert detected.entry_point is not None

    written = write_build_definition(repo, detected)
    sources = _add_executable_sources(written.read_text())

    entry = str(detected.entry_point.relative_to(detected.root))
    assert entry in sources, "the chosen entry point must be built"

    mains = {"bench/bench.c", "tests/test_lib.c", "tools/tool.c"}
    included = mains & set(sources)
    assert included == {entry}, (
        f"exactly one main() may be linked; got {sorted(included)}"
    )
    # The non-entry translation units are still required.
    assert "src/lib.c" in sources, "ordinary sources must not be dropped"


def test_the_excluded_entry_points_are_named(tmp_path):
    """Dropping a file from the build silently is its own failure mode.

    If the wrong main was picked, the user needs to be told which ones were
    left out to know that is what happened.
    """
    repo = _tree(tmp_path / "repo", {
        "src/lib.h": LIB_H,
        "src/lib.c": LIB_C,
        "bench/bench.c": BENCH_C,
        "tests/test_lib.c": TEST_C,
    })
    detected = detect(repo)
    blob = " ".join(detected.warnings)
    assert "define main()" in blob
    assert "test_lib.c" in blob, "the omitted entry point must be named"


def test_a_single_main_project_is_unaffected(tmp_path):
    """The common case must not lose sources to the exclusion."""
    repo = _tree(tmp_path / "repo", {
        "src/lib.h": LIB_H,
        "src/lib.c": LIB_C,
        "bench/bench.c": BENCH_C,
    })
    detected = detect(repo)
    sources = _add_executable_sources(write_build_definition(repo, detected).read_text())
    assert set(sources) == {"bench/bench.c", "src/lib.c"}
    assert not any("define main()" in w for w in detected.warnings)


@pytest.mark.slow
@pytest.mark.skipif(
    not (shutil.which("cmake") and shutil.which("cc")), reason="needs cmake and a C compiler"
)
def test_the_generated_build_actually_links(tmp_path):
    """The assertion that matters: it compiles.

    Checking the source list is a proxy. This is the property the proxy stands
    in for, and it is what failed in production.
    """
    repo = _tree(tmp_path / "repo", {
        "src/lib.h": LIB_H,
        "src/lib.c": LIB_C,
        "bench/bench.c": BENCH_C,
        "tests/test_lib.c": TEST_C,
        "tools/tool.c": TOOL_C,
    })
    write_build_definition(repo, detect(repo))

    build = tmp_path / "build"
    configure = subprocess.run(
        ["cmake", "-S", str(repo), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert configure.returncode == 0, configure.stderr[-800:]

    compile_ = subprocess.run(
        ["cmake", "--build", str(build), "-j4"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert compile_.returncode == 0, (
        "generated build did not link:\n" + (compile_.stderr or compile_.stdout)[-1200:]
    )
    assert "multiple definition of" not in (compile_.stderr + compile_.stdout)

    program = build / "program"
    assert program.exists(), "add_executable(program ...) produced no binary"
    run = subprocess.run([str(program)], capture_output=True, text=True, timeout=120, check=False)
    assert run.returncode == 0, run.stderr[-400:]


def _add_executable_sources(cmakelists: str) -> list[str]:
    for line in cmakelists.splitlines():
        if line.startswith("add_executable("):
            inner = line[len("add_executable("):].rstrip(")")
            return inner.split()[1:]  # drop the target name
    raise AssertionError("no add_executable() in the generated CMakeLists")
