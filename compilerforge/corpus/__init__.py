"""Task packages, corpus manifests and the equivalence comparators."""

from compilerforge.corpus.equivalence import (
    EVIDENCE_SCOPE_NOTICE,
    ComparisonResult,
    Divergence,
    Observation,
    comparator_for,
)
from compilerforge.corpus.package import (
    Corpus,
    LoadedPackage,
    ReferenceOptimization,
    TaskPackage,
    WorkloadProfile,
    content_hash_tree,
    inventory_hash,
)

__all__ = [
    "EVIDENCE_SCOPE_NOTICE",
    "ComparisonResult",
    "Corpus",
    "Divergence",
    "LoadedPackage",
    "Observation",
    "ReferenceOptimization",
    "TaskPackage",
    "WorkloadProfile",
    "comparator_for",
    "content_hash_tree",
    "inventory_hash",
]
