import codecs
import os
import re
from pathlib import Path

from setuptools import find_packages, setup

HERE = Path(__file__).parent


def read_requirements(path: str) -> list[str]:
    requirements: list[str] = []
    for line in (HERE / path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("git+"):
            # Direct VCS references are installable but not valid metadata for
            # install_requires; keep the package name only.
            requirements.append(line.split("#egg=")[-1])
        else:
            requirements.append(line)
    return requirements


def read_version() -> str:
    init = codecs.open(
        os.path.join(HERE, "compilerforge", "__init__.py"), encoding="utf-8"
    ).read()
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', init, re.MULTILINE)
    if match is None:
        raise RuntimeError("Unable to find __version__ in compilerforge/__init__.py")
    return match.group(1)


setup(
    name="compilerforge",
    version=read_version(),
    description="Verified autonomous software performance engineering on Bittensor",
    long_description=(HERE / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/compilerforge/compilerforge",
    author="CompilerForge",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    python_requires=">=3.11",
    install_requires=read_requirements("requirements.txt"),
    extras_require={
        "dev": ["pytest>=8.0", "pytest-asyncio>=0.24", "ruff>=0.6"],
    },
    entry_points={
        "console_scripts": [
            "cf-eval=compilerforge.sdk.cli:app",
            "cf-corpus=compilerforge.corpus.cli:app",
            "cf-miner=compilerforge.miner.cli:app",
            "cf-validator=compilerforge.validator.cli:app",
        ],
    },
    license="MIT",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Compilers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
