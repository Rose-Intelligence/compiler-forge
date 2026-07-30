"""``task.json`` — the contract the validator hands the agent (Appendix A.1).

This is the only thing a miner artifact reads. Keeping it narrow keeps the sandbox
surface small and keeps the harness comparison fair: two agents that see the same
bytes here had the same information.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from compilerforge.spec import INTERFACE_VERSION, SPEC, SPEC_VERSION


class Objective(StrEnum):
    RUNTIME = "runtime"
    MEMORY = "memory"
    TAIL_LATENCY = "tail_latency"
    ENERGY = "energy"
    BALANCED = "balanced"


class EquivalenceDiscipline(StrEnum):
    """Differential testing must be domain-specific.

    The task package declares which discipline applies; the agent does not get to
    choose, because choosing a laxer comparator is the cheapest possible attack.
    """

    BYTE_EQUAL = "byte_equal"
    ROUND_TRIP = "round_trip"
    FLOAT_TOLERANCE = "float_tolerance"
    STRUCTURAL = "structural"
    STATE_INVARIANT = "state_invariant"
    OPERATION_SEQUENCE = "operation_sequence"


class WorkloadFamily(StrEnum):
    """V1 spans 5-7 families across 20-30 repositories."""

    COMPRESSION = "compression"
    SERIALISATION = "serialisation"
    PARSING = "parsing"
    NUMERICAL = "numerical"
    IMAGE = "image"
    DATA_STRUCTURES = "data_structures"
    NETWORKING = "networking"
    CLI_UTILITIES = "cli_utilities"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RepositoryContract(_Frozen):
    uri: str = Field(description="Always a mounted:// path during an authoritative run.")
    revision: str
    license: str

    @field_validator("uri")
    @classmethod
    def _must_be_mounted(cls, v: str) -> str:
        # A task that hands the agent a remote URI has already lost the network
        # isolation argument.
        if not v.startswith("mounted://"):
            raise ValueError("repository uri must be a mounted:// path during an authoritative run")
        return v


class BuildContract(_Frozen):
    command: str
    toolchain_digest: str
    forbidden_flags: tuple[str, ...] = ()
    # Second build used by the UB cross-check.
    second_opt_level: str = SPEC.safety.second_opt_level
    #: The pinned compilers, declared so an agent does not have to guess.
    #:
    #: Without these the build system picks whatever it finds first — `cc`, which
    #: is gcc on a stock Ubuntu host — while the toolchain digest is computed from
    #: clang. An agent that optimizes against one compiler and is measured under
    #: another is being scored on a different program than it tuned.
    c_compiler: str = "clang"
    cxx_compiler: str = "clang++"


class PatchScope(_Frozen):
    """Which files a patch is allowed to touch.

    An allowlist, not a denylist. A denylist of "protected" paths has to
    enumerate every file that could be abused, and it will always miss one: a
    patch that moves the instrumentation markers, for example, can score the
    maximum possible capture while optimizing nothing.

    Anything not matched here is validator-owned and immutable, so a package that
    grows a new directory defaults to protected rather than open.
    """

    #: Glob patterns, relative to the repository root.
    patchable: tuple[str, ...] = ("src/**", "include/**")
    #: Hash over every file outside `patchable`, taken from the pristine package.
    #: Verified after the patch applies, which catches additions and deletions
    #: that a pattern check on the diff alone would miss.
    immutable_hash: str = ""


class TestContract(_Frozen):
    public_command: str
    hidden_contract: Literal["validator-owned"] = "validator-owned"
    #: Hash over the sorted (path, sha256) inventory of every test file. Verified
    #: rather than merely counting files, so renaming a test to death is caught.
    inventory_hash: str
    #: The harness that reads one hidden input on stdin and prints every declared
    #: observable. Separate from the benchmark: the benchmark exists to be
    #: expensive and is driven by arguments, whereas this exists to be
    #: comprehensive and is driven by input. Using one for the other silently
    #: compares the wrong thing.
    differential_command: str | None = None


class EquivalenceContract(_Frozen):
    discipline: EquivalenceDiscipline
    float_tolerance_ulp: int | None = None
    relative_error_budget: float | None = None
    side_effects: tuple[str, ...] = ("stdout", "exit_code")

    @field_validator("side_effects")
    @classmethod
    def _non_empty(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("at least one observable side effect must be declared")
        return v


#: The bracketed region: Callgrind starts with --instr-atstart=no and the
#: benchmark turns instrumentation on around the code that matters. Process
#: startup and input generation are excluded, so a small kernel's real cost is
#: not swamped by setup.
MARKED_REGION = "cf_bench_start/cf_bench_stop"

#: Everything the process does, startup included. Correct but blunter: it is the
#: only option for a benchmark that cannot be edited to carry the markers, and it
#: dilutes an improvement by whatever fixed cost surrounds it.
WHOLE_PROCESS = "whole_process"


class BenchmarkContract(_Frozen):
    command: str
    objective: Objective = Objective.BALANCED
    #: Which of the two measurement modes above applies. The package declares it;
    #: the runner is configured from it and never guesses.
    measured_region: str = MARKED_REGION
    max_wall_seconds: int = SPEC.budget.default_wall_seconds

    @field_validator("measured_region")
    @classmethod
    def _known_region(cls, v: str) -> str:
        if v not in (MARKED_REGION, WHOLE_PROCESS):
            raise ValueError(
                f"measured_region must be {MARKED_REGION!r} or {WHOLE_PROCESS!r}, "
                f"got {v!r}"
            )
        return v

    @property
    def instrument_at_start(self) -> bool:
        """True when there are no markers, so the whole process is counted."""
        return self.measured_region == WHOLE_PROCESS


class ResourceContract(_Frozen):
    cpu_cores: int = SPEC.budget.default_cpu_cores
    ram_gb: int = SPEC.budget.default_ram_gb
    disk_gb: int = SPEC.budget.default_disk_gb
    pids_max: int = SPEC.budget.default_pids_max
    model_token_budget: int = SPEC.budget.default_model_token_budget


class Task(_Frozen):
    """The full instance handed to one artifact for one evaluation."""

    spec_version: int = SPEC_VERSION
    interface_version: str = INTERFACE_VERSION
    task_id: str

    repository: RepositoryContract
    build: BuildContract
    tests: TestContract
    equivalence: EquivalenceContract
    benchmark: BenchmarkContract
    resources: ResourceContract = Field(default_factory=ResourceContract)

    #: Derived from a future block hash. The agent must honour it
    #: via CF_SEED so a replay audit can reproduce the run.
    #: What the patch may change. Everything else belongs to the validator.
    patch_scope: PatchScope = Field(default_factory=PatchScope)

    seed: str
    network: Literal["none", "proxy"] = "none"

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
