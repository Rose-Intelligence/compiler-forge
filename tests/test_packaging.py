"""Package metadata must match what the code actually needs.

This file exists because of a bug that reached `main`: `PyYAML` was listed in
`requirements.txt` but not in `pyproject.toml`, so `pip install -e .` produced a
package that raised `ModuleNotFoundError` on `import compilerforge.corpus`.

Nothing caught it. Every one of the 164 tests passed, because the development
environment already had PyYAML installed for unrelated reasons — the tests
exercise behaviour, and a dependency that happens to be present looks identical
to one that is declared.

These tests read the declarations rather than the environment, so they fail in
the same way everywhere: on a laptop that happens to have the package, and on a
clean CI runner that does not.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "compilerforge"

#: import name -> distribution name, where they differ.
DISTRIBUTION_OF = {
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
}

#: Declared on purpose despite never being imported by this package. Each entry
#: needs a reason, so that "unused" and "used somewhere a scanner cannot see"
#: stay distinguishable.
INTENTIONALLY_UNIMPORTED = {
    "uvicorn": "serves the metered inference proxy; the operator runs it, this package does not import it",
}


def _normalise(name: str) -> str:
    return name.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower()


def _declared_in_pyproject() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return {_normalise(d) for d in data["project"]["dependencies"]}


def _declared_in_requirements() -> set[str]:
    return {
        _normalise(line)
        for line in (REPO / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _third_party_imports() -> dict[str, set[str]]:
    """Top-level third-party modules imported anywhere in the package."""
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, set[str]] = {}

    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import; module None is `from . import x`
                names = (
                    [node.module.split(".")[0]]
                    if node.module and node.level == 0
                    else []
                )
            else:
                continue

            for name in names:
                if name and name not in stdlib and name != "compilerforge":
                    found.setdefault(name, set()).add(
                        str(path.relative_to(REPO))
                    )
    return found


def test_every_import_is_declared_in_pyproject():
    """The bug this file exists for.

    pyproject.toml is what `pip install` reads, so a module imported by the
    package and missing from it is a broken install for every user.
    """
    declared = _declared_in_pyproject()
    undeclared = {
        module: sorted(files)
        for module, files in _third_party_imports().items()
        if DISTRIBUTION_OF.get(module, module).lower() not in declared
    }

    assert not undeclared, (
        "these modules are imported but not declared in pyproject.toml, so "
        "`pip install` produces a broken package:\n"
        + "\n".join(f"  {m} — imported by {f[0]}" for m, f in sorted(undeclared.items()))
    )


def test_every_import_is_declared_in_requirements():
    """requirements.txt is what the deployment runbooks tell operators to use."""
    declared = _declared_in_requirements()
    undeclared = {
        module
        for module in _third_party_imports()
        if DISTRIBUTION_OF.get(module, module).lower() not in declared
    }
    assert not undeclared, f"imported but missing from requirements.txt: {sorted(undeclared)}"


def test_the_two_dependency_lists_agree():
    """They drifted once, which is how PyYAML ended up in one and not the other."""
    only_pyproject = _declared_in_pyproject() - _declared_in_requirements()
    only_requirements = _declared_in_requirements() - _declared_in_pyproject()
    assert not only_pyproject, f"in pyproject.toml only: {sorted(only_pyproject)}"
    assert not only_requirements, f"in requirements.txt only: {sorted(only_requirements)}"


def test_no_dependency_is_declared_without_being_used():
    """An unused dependency is a slower install and a larger attack surface.

    Anything genuinely needed but not imported — a server, a plugin — belongs in
    INTENTIONALLY_UNIMPORTED with a reason, so the exception is a decision rather
    than an oversight.
    """
    imported = {
        DISTRIBUTION_OF.get(module, module).lower() for module in _third_party_imports()
    }
    allowed = {name.lower() for name in INTENTIONALLY_UNIMPORTED}
    unused = _declared_in_pyproject() - imported - allowed

    assert not unused, (
        f"declared but never imported: {sorted(unused)}. Remove them, or add "
        "them to INTENTIONALLY_UNIMPORTED with the reason they are needed."
    )


# ---------------------------------------------------------------------------
# console scripts
# ---------------------------------------------------------------------------


def _console_scripts() -> dict[str, str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    return data["project"]["scripts"]


def test_every_console_script_target_is_importable():
    """Closes the gap that shipped a broken `cf-validator` for sixteen commits.

    An entry point is only resolved when the script is invoked, so a moved
    module leaves a command on PATH that dies with ImportError. `pip install`
    succeeds, and nothing else in the test suite goes through these paths.
    """
    import importlib

    broken = {}
    for script, target in _console_scripts().items():
        module_path, _, attribute = target.partition(":")
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 - the message is the diagnostic
            broken[script] = f"{target} — {type(exc).__name__}: {exc}"
            continue
        if attribute and not hasattr(module, attribute):
            broken[script] = f"{target} — module has no attribute {attribute!r}"

    assert not broken, "console scripts pointing at nothing:\n" + "\n".join(
        f"  {s}: {why}" for s, why in sorted(broken.items())
    )


def test_setup_py_and_pyproject_declare_the_same_scripts():
    """Both files declare entry points, and they drifted once already."""
    setup_text = (REPO / "setup.py").read_text()
    for script, target in _console_scripts().items():
        assert f'"{script}={target}"' in setup_text, (
            f"{script} is declared in pyproject.toml as {target} but not "
            "identically in setup.py"
        )


@pytest.mark.slow
def test_the_package_installs_and_imports_in_a_clean_environment():
    """The end-to-end version of the check, in a venv with nothing preinstalled.

    Slow because it builds a virtualenv and resolves the real dependency tree.
    This is the only test that would have caught the PyYAML bug without knowing
    to look for it.
    """
    import tempfile
    import venv

    with tempfile.TemporaryDirectory() as tmp:
        env_dir = Path(tmp) / "venv"
        venv.create(env_dir, with_pip=True)
        python = env_dir / "bin" / "python"

        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "-e", str(REPO)],
            capture_output=True,
            text=True,
        )
        assert install.returncode == 0, f"pip install failed:\n{install.stderr[-2000:]}"

        probe = (
            "import importlib, pkgutil, sys, compilerforge\n"
            "bad = []\n"
            "for m in pkgutil.walk_packages(compilerforge.__path__, 'compilerforge.'):\n"
            "    try:\n"
            "        importlib.import_module(m.name)\n"
            "    except Exception as exc:\n"
            "        bad.append(f'{m.name}: {type(exc).__name__}: {exc}')\n"
            "print(chr(10).join(bad))\n"
            "sys.exit(1 if bad else 0)\n"
        )
        check = subprocess.run(
            [str(python), "-c", probe], capture_output=True, text=True
        )
        assert check.returncode == 0, (
            "the package installs but does not import cleanly:\n" + check.stdout
        )


# ---------------------------------------------------------------------------
# the commands the documentation tells people to run
# ---------------------------------------------------------------------------


def test_the_sdk_cli_exposes_the_commands_the_readme_documents():
    """A README that names a command the CLI does not have wastes someone's hour.

    The console scripts are checked above for pointing at a real module; this
    checks the module actually defines the subcommands the quick start uses.
    """
    from compilerforge.sdk.cli import app

    names = {command.name or command.callback.__name__ for command in app.registered_commands}
    for expected in ("onboard", "verify", "patch", "gates", "spec", "preflight"):
        assert expected in names, f"cf-eval has no {expected!r} command: {sorted(names)}"


def test_the_readme_only_names_commands_that_exist():
    readme = (REPO / "README.md").read_text()
    declared = set(_console_scripts())
    for line in readme.splitlines():
        stripped = line.strip()
        if not stripped.startswith("cf-"):
            continue
        command = stripped.split()[0].rstrip("`")
        assert command in declared, (
            f"README runs {command!r}, which is not a declared console script. "
            f"Declared: {sorted(declared)}"
        )
