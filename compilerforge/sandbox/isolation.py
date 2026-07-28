"""The isolation contract for running untrusted miner code (12.3).

The validator holds a hotkey and hidden task material on the same machine that
runs arbitrary code submitted by an anonymous competitor. Everything here exists
because of that sentence.

Container flags are necessary but explicitly *not sufficient*. ``Runtime`` selects
between a shared-kernel container and a real isolation boundary — gVisor's
user-space kernel or a Firecracker/Kata microVM — and ``IsolationProfile.assert_safe``
refuses to run an authoritative phase on the weakest option.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from compilerforge.protocol.task import ResourceContract


class Runtime(StrEnum):
    RUNC = "runc"          # shared kernel — development only
    RUNSC = "runsc"        # gVisor user-space kernel
    KATA = "kata-runtime"  # microVM
    FIRECRACKER = "firecracker"

    @property
    def is_hardened(self) -> bool:
        return self is not Runtime.RUNC


class Phase(StrEnum):
    """Preparation may use controlled mirrors; the authoritative phases may not."""

    PREPARE = "prepare"
    OPTIMIZE = "optimize"
    EVALUATE = "evaluate"

    @property
    def is_authoritative(self) -> bool:
        return self in (Phase.OPTIMIZE, Phase.EVALUATE)


class NetworkMode(StrEnum):
    NONE = "none"
    #: Loopback-only, metered, validator-owned inference proxy. Nothing else.
    PROXY = "proxy"
    #: Pinned internal package mirrors, preparation phase only.
    MIRROR = "mirror"


class IsolationError(RuntimeError):
    """Raised when a requested configuration would breach the security boundary."""


@dataclass(frozen=True, slots=True)
class IsolationProfile:
    """One fully-specified execution environment."""

    runtime: Runtime
    phase: Phase
    network: NetworkMode
    resources: ResourceContract
    wall_seconds: int

    read_only_rootfs: bool = True
    drop_all_capabilities: bool = True
    no_new_privileges: bool = True
    run_as_user: str = "65534:65534"  # nobody:nogroup
    seccomp_profile: str | None = None
    #: 1 GiB. A fork bomb is cheap; so is capping the file size it can write.
    max_file_size_bytes: int = 1024**3

    def assert_safe(self, *, allow_unhardened: bool = False) -> None:
        """Fail closed on any configuration that breaks the security boundary.

        ``allow_unhardened`` is a development-only override (the operator passed
        ``--sandbox.allow_unhardened_runtime``) that permits a shared-kernel
        runtime for the authoritative phases. It is unsafe against untrusted
        miner code and must never be set on a public validator.
        """
        if self.phase.is_authoritative:
            if self.network is NetworkMode.MIRROR:
                raise IsolationError(
                    "package mirrors are a preparation-phase affordance; the authoritative "
                    "phases run with no external network"
                )
            if not self.runtime.is_hardened and not allow_unhardened:
                raise IsolationError(
                    f"runtime {self.runtime} shares the host kernel; authoritative phases "
                    "require gVisor or a microVM (sandbox escape)"
                )
        for flag, name in (
            (self.read_only_rootfs, "read_only_rootfs"),
            (self.drop_all_capabilities, "drop_all_capabilities"),
            (self.no_new_privileges, "no_new_privileges"),
        ):
            if not flag:
                raise IsolationError(f"{name} may not be disabled")
        uid = self.run_as_user.split(":", 1)[0].strip()
        if uid in ("0", "root"):
            raise IsolationError("artifacts never run as root")

    def container_args(self) -> list[str]:
        """Docker/Podman arguments implementing this profile.

        Deliberately explicit rather than assembled from a config file: an operator
        reading this list should be able to see the whole boundary at once.
        """
        args = [
            f"--runtime={self.runtime.value}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={self.run_as_user}",
            f"--pids-limit={self.resources.pids_max}",
            f"--memory={self.resources.ram_gb}g",
            # No swap: a memory cap that can be escaped into swap is not a cap.
            f"--memory-swap={self.resources.ram_gb}g",
            f"--cpus={self.resources.cpu_cores}",
            f"--ulimit=fsize={self.max_file_size_bytes}",
            f"--ulimit=nproc={self.resources.pids_max}",
            # No host devices, no host IPC, no host PID namespace.
            "--ipc=private",
            "--cgroupns=private",
        ]
        if self.seccomp_profile:
            args.append(f"--security-opt=seccomp={self.seccomp_profile}")

        match self.network:
            case NetworkMode.NONE:
                args.append("--network=none")
            case NetworkMode.PROXY:
                # An internal bridge reaching exactly one address: the validator's
                # metered proxy. The artifact cannot resolve or route anywhere else.
                args.append("--network=cf-proxy")
                args.append("--dns=0.0.0.0")
            case NetworkMode.MIRROR:
                args.append("--network=cf-mirror")

        return args

    def forbidden_mounts(self) -> tuple[str, ...]:
        """Paths that must never appear in any bind mount for an artifact.

        Checked by ``assert_mounts_safe`` rather than trusted to review.
        """
        return (
            "/var/run/docker.sock",
            "/run/docker.sock",
            "~/.bittensor",
            "/root/.bittensor",
            "/etc/shadow",
        )

    def assert_mounts_safe(self, mounts: dict[str, str]) -> None:
        forbidden = tuple(str(Path(b).expanduser()).rstrip("/") for b in self.forbidden_mounts())

        def hits(candidate: str) -> bool:
            return any(candidate == bad or candidate.startswith(bad + "/") for bad in forbidden)

        for host_path in mounts:
            expanded = str(Path(host_path).expanduser()).rstrip("/")
            # Resolve symlinks as well, so a link that points at a forbidden path
            # cannot smuggle it in under an innocent-looking name.
            resolved = str(Path(host_path).expanduser().resolve()).rstrip("/")
            if hits(expanded) or hits(resolved):
                raise IsolationError(f"refusing to mount {host_path} into an artifact")


def default_profile(
    *,
    phase: Phase,
    resources: ResourceContract,
    wall_seconds: int,
    network: NetworkMode = NetworkMode.NONE,
    runtime: Runtime | None = None,
) -> IsolationProfile:
    """Build the standard profile, picking the strongest runtime available."""
    resolved = runtime if runtime is not None else detect_runtime()
    profile = IsolationProfile(
        runtime=resolved,
        phase=phase,
        network=network,
        resources=resources,
        wall_seconds=wall_seconds,
    )
    return profile


def detect_runtime() -> Runtime:
    """Prefer a real isolation boundary; fall back only to something visible."""
    if shutil.which("runsc"):
        return Runtime.RUNSC
    if shutil.which("kata-runtime"):
        return Runtime.KATA
    if shutil.which("firecracker"):
        return Runtime.FIRECRACKER
    return Runtime.RUNC
