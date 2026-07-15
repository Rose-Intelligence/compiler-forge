"""Subnet hyperparameters and registration facts.

The plan is kept as data rather than prose so a validator can assert against live
chain values instead of trusting a runbook that drifted. ``check_live`` is what
turns this file from documentation into a preflight check.

Two chain-side constraints shape operations. Each hyperparameter can be changed
roughly once per two tempos, tracked independently per parameter; and owner admin
calls are rejected during the last few blocks of each tempo while the epoch is
computed. At a 24-hour tempo a parameter change therefore has a two-day cooldown,
so configuration mistakes are expensive and testnet rehearsal is not optional.
"""

from __future__ import annotations

from dataclasses import dataclass

BLOCK_SECONDS = 12
BLOCKS_PER_DAY = 7200
BLOCKS_PER_HOUR = 300


@dataclass(frozen=True, slots=True)
class HyperparameterSetting:
    name: str
    chain_default: str
    compilerforge: str
    reason: str


HYPERPARAMETER_PLAN: tuple[HyperparameterSetting, ...] = (
    HyperparameterSetting(
        "tempo",
        "360",
        "7200",
        "A full round — build, differential, fuzz, sanitize, deterministic "
        "measurement and wall-clock across a corpus — cannot complete in 72 "
        "minutes. The owner-settable range is 360-50,400 blocks.",
    ),
    HyperparameterSetting(
        "commit_reveal_weights_enabled",
        "true",
        "true",
        "Weight copying is the specific failure that would make independent "
        "measurement worthless. Keep it on.",
    ),
    HyperparameterSetting(
        "commit_reveal_period",
        "1",
        "1",
        "One round of concealment is sufficient at a long tempo.",
    ),
    HyperparameterSetting(
        "max_allowed_uids",
        "256",
        "128",
        "Required: max_allowed_uids x mechanism_count must not exceed 256, and "
        "this subnet runs two mechanisms.",
    ),
    HyperparameterSetting(
        "immunity_period",
        "4096",
        "14400",
        "A new miner must survive at least one full evaluation round before it "
        "can be pruned, or good artifacts are evicted before they are scored.",
    ),
    HyperparameterSetting(
        "activity_cutoff_factor",
        "13889",
        "verify at the chosen tempo",
        "Effective cutoff = factor x tempo / 1000, bounded to 1,000-50,000 "
        "blocks. At a long tempo this must be checked explicitly, or validators "
        "between scoring runs are silently excluded from consensus.",
    ),
    HyperparameterSetting(
        "yuma3_enabled",
        "false",
        "evaluate before launch",
        "Changes bond and dividend computation. Test on testnet, and never "
        "change it during a live competition.",
    ),
    HyperparameterSetting(
        "liquid_alpha_enabled",
        "false",
        "false",
        "Only takes effect with Yuma3 on, and adds a dynamic bond EMA that is "
        "hard to reason about while the mechanism is still being calibrated.",
    ),
    HyperparameterSetting(
        "recycle_or_burn",
        "burn",
        "recycle",
        "Under the floor-and-decay emission policy, anything ever directed at "
        "burn UIDs should return to the pool rather than leave the system.",
    ),
    HyperparameterSetting(
        "owner_cut_auto_lock_enabled",
        "false",
        "consider true",
        "Locks the owner cut into a conviction position instead of paying free "
        "stake — a credible public signal that the operator is not selling "
        "emissions.",
    ),
)


def effective_activity_cutoff(tempo_blocks: int, factor_per_mille: int = 13889) -> int:
    """Blocks of inactivity before a validator drops out of consensus."""
    raw = factor_per_mille * tempo_blocks // 1000
    return max(1000, min(raw, 50000))


def heartbeat_required(tempo_blocks: int, factor_per_mille: int = 13889) -> bool:
    """Whether a validator idle between rounds would fall out of consensus."""
    return tempo_blocks > effective_activity_cutoff(tempo_blocks, factor_per_mille) // 2


def check_live(chain) -> list[str]:
    """Compare the live chain configuration against the plan.

    Takes a :class:`~compilerforge.chain.access.ChainAccess`. Returns
    human-readable discrepancies; an empty list means the subnet is configured
    the way this software expects.

    A failure to read is itself reported as a discrepancy rather than an empty
    list — "we could not check" and "everything is fine" must never look the same.
    """
    from compilerforge.chain.access import ChainError

    problems: list[str] = []
    try:
        params = chain.hyperparameters()
    except ChainError as exc:
        return [f"could not read hyperparameters: {exc}"]

    def get(*names: str):
        for name in names:
            if name in params and params[name] is not None:
                return params[name]
        return None

    tempo = _as_int(get("tempo"))
    if tempo is None:
        problems.append("chain reported no tempo; cannot validate round length")
    elif tempo < 3600:
        problems.append(
            f"tempo is {tempo} blocks (~{tempo * BLOCK_SECONDS / 3600:.1f}h); a full "
            "evaluation round needs considerably longer"
        )

    commit_reveal = get("commit_reveal_weights_enabled", "commit_reveal_enabled")
    if commit_reveal is not None and not bool(commit_reveal):
        problems.append(
            "commit_reveal_weights_enabled is off; validators can copy each other's "
            "weights, which makes independent measurement worthless"
        )

    max_uids = _as_int(get("max_allowed_uids", "max_uids"))
    if max_uids is not None and max_uids > 128:
        problems.append(
            f"max_allowed_uids is {max_uids}; a two-mechanism subnet must stay at or "
            "below 128"
        )

    if tempo is not None:
        cutoff = effective_activity_cutoff(tempo)
        if tempo > cutoff:
            problems.append(
                f"tempo {tempo} exceeds the {cutoff}-block activity cutoff; validators "
                "will be excluded from consensus between rounds without a heartbeat"
            )

    return problems


def _as_int(value) -> int | None:
    """Coerce a hyperparameter to int, or None when it is absent or not a number.

    Returns None rather than a default: a check that silently reads a missing
    tempo as zero would report a healthy subnet as misconfigured, and one that
    read it as a large number would report a misconfigured subnet as healthy.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


REGISTRATION_NOTES = """\
Registering a subnet is capital deployment, not a fee.

The entire lock transfers into the new subnet's pool as its initial TAO reserve,
floored at the network minimum. It is recovered only by owning a subnet that
earns. The price doubles on each successful registration network-wide and decays
linearly over roughly two weeks, and registrations are rate-limited network-wide,
so entry timing is a real cost lever. Always read it live:

    btcli query subnet-registration-cost --json

New subnets are immune from price-ranked deregistration for about six months.
That immunity window is the entire runway: after it expires, the subnet with the
lowest EMA alpha price is pruned whenever someone new registers. Everything in the
launch plan should be scheduled against that clock.

Emission is off at registration. The emission switch belongs to root and owners
cannot set it, so plan for a testing window with a live, active, zero-emission
subnet before anything economic happens.

Make the owner coldkey a multisig from the start and grant a dedicated operations
key a narrow proxy for routine hyperparameter changes. Migrating later requires an
announced coldkey swap, which is strictly more painful.
"""
