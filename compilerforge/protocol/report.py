"""``/output/report.json`` — what the agent hands back.

Nothing in this file is authoritative. ``self_measurement`` in particular is never
scored; it is retained to calibrate how well agents predict their own results and
to detect agents that systematically overstate.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from compilerforge.protocol.task import Objective
from compilerforge.spec import INTERFACE_VERSION


class SelfMeasurement(BaseModel):
    model_config = ConfigDict(extra="allow")

    local_speedup_estimate: float | None = None
    method: str | None = None


class BudgetUsed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wall_seconds: float = 0.0
    model_tokens: int = 0


class AgentReport(BaseModel):
    """Parsed leniently: a malformed report is an interface violation, but an
    unexpected extra field is not worth zeroing an otherwise valid patch."""

    model_config = ConfigDict(extra="allow")

    interface_version: str = INTERFACE_VERSION
    task_id: str
    agent_version: str = "0.0.0"
    objective: Objective = Objective.BALANCED

    changed_files: list[str] = Field(default_factory=list)
    claimed_strategy: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    selected_candidate: int | None = None
    rejected_reasons: dict[str, str] = Field(default_factory=dict)

    self_measurement: SelfMeasurement = Field(default_factory=SelfMeasurement)
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    notes: str = "Informational only. Validator measurements are authoritative."

    def overstatement(self, measured_speedup: float) -> float | None:
        """How much the agent overstated its own result, for calibration only.

        Positive means the agent claimed more than the validator measured.
        """
        claimed = self.self_measurement.local_speedup_estimate
        if claimed is None or measured_speedup <= 0:
            return None
        return claimed - measured_speedup
