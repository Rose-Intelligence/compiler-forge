"""CompilerForge — verified autonomous software performance engineering.

A permissionless research network for agents that make existing software faster,
lighter and cheaper without changing what it does.

Miners submit reusable, immutable optimization agents. Validators execute those
agents on repositories and workloads the miner has never seen, then measure the
result on their own instrumentation. Emissions flow only for improvements a
validator reproduced itself.

Package layout::

    compilerforge.protocol    wire contracts between miners, validators and chain
    compilerforge.corpus      task packages, manifests, equivalence comparators
    compilerforge.sandbox     untrusted artifact execution and the inference proxy
    compilerforge.evaluation  the gate sequence and the two measurement tiers
    compilerforge.scoring     capture, aggregation, dethronement, emissions
    compilerforge.chain       commitments, sealed task material, public audit
    compilerforge.base        base neuron classes
    compilerforge.validator   the validator neuron and its round machinery
    compilerforge.miner       the miner neuron and the reference agent
    compilerforge.sdk         the local evaluator miners develop against
"""

__version__ = "0.1.0"

# Bumped whenever the evaluation contract changes in a way that makes older
# scores incomparable. Submitted as the version key with every weight vector, so
# validators running different contracts are distinguishable on chain.
__spec_version__ = 1000

from compilerforge.spec import INTERFACE_VERSION, SPEC, SPEC_VERSION  # noqa: E402

__all__ = [
    "INTERFACE_VERSION",
    "SPEC",
    "SPEC_VERSION",
    "__spec_version__",
    "__version__",
]
