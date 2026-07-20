"""Client for the validator's metered inference proxy.

The agent reaches the proxy over loopback and nothing else — there is no external
network during an authoritative run. Every call is counted against the task's
token budget by the validator, so a harness that plans its search will get more
out of the budget than one that discovers the wall by hitting it.

``/v1/budget`` exists precisely for that: check what remains before committing to
an expensive candidate-generation pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from compilerforge.miner.reference_agent.agent import Candidate, Hotspot


class BudgetExhausted(RuntimeError):
    """The token budget is spent. Return the best surviving candidate and stop."""


@dataclass(slots=True)
class InferenceClient:
    base_url: str
    run_id: str
    timeout: float = 180.0

    @classmethod
    def from_environment(cls) -> InferenceClient | None:
        """Build a client if the validator configured a proxy for this run."""
        url = os.getenv("CF_INFERENCE_URL")
        if not url:
            return None
        return cls(base_url=url.rstrip("/"), run_id=os.getenv("CF_TASK_ID", ""))

    def remaining_tokens(self) -> int:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}/v1/budget", headers={"x-cf-run-id": self.run_id}
            )
            response.raise_for_status()
            return int(response.json()["remaining"])

    def complete(self, messages: list[dict[str, Any]], *, max_tokens: int = 2048) -> str:
        """One chat completion. The model is pinned by the validator."""
        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers={"x-cf-run-id": self.run_id},
            )
            if response.status_code == 429:
                raise BudgetExhausted(response.text)
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]

    def propose_candidates(
        self, tree: Path, hotspots: list[Hotspot], task: dict
    ) -> list[Candidate]:
        """Ask the pinned model for transformations of the hottest source file.

        This is the extension point a competitive harness would build on: better
        decomposition, better context selection, self-critique, and a ranking pass
        over many more candidates than this returns.
        """
        from compilerforge.miner.reference_agent.agent import Candidate

        if not hotspots:
            return []

        source_name = hotspots[0].source_file
        if not source_name:
            return []
        matches = list(tree.rglob(Path(source_name).name))
        if not matches:
            return []
        path = matches[0]
        rel = str(path.relative_to(tree))
        original = path.read_text(errors="replace")

        prompt = _CANDIDATE_PROMPT.format(
            objective=task.get("benchmark", {}).get("objective", "balanced"),
            discipline=task.get("equivalence", {}).get("discipline", "byte_equal"),
            hotspots="\n".join(f"- {h}" for h in hotspots[:5]),
            path=rel,
            source=original[:20000],
        )

        try:
            reply = self.complete(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=8192,
            )
        except BudgetExhausted:
            return []

        return [
            Candidate(
                name=f"model:{rel}:{i}",
                strategy=item.get("strategy", "model-proposed"),
                files={rel: item["source"]},
                rationale=item.get("rationale", ""),
            )
            for i, item in enumerate(_parse_candidates(reply))
            if item.get("source")
        ]


_SYSTEM_PROMPT = """\
You are a performance engineer. You make existing C/C++ code cheaper to run without
changing what it does. You never weaken a test, never introduce undefined behaviour,
and never claim a speedup you have not measured. If you cannot find a safe
improvement, say so — an honest empty result is better than a rejected patch.
"""

_CANDIDATE_PROMPT = """\
Objective: {objective}
Equivalence discipline the patch must preserve: {discipline}

Profile (instruction share by function):
{hotspots}

File: {path}
```c
{source}
```

Propose up to three distinct, behaviour-preserving optimizations. Return JSON only:

{{"candidates": [
  {{"strategy": "short label", "rationale": "why this is cheaper and why it is safe",
    "source": "the complete rewritten file"}}
]}}
"""


def _parse_candidates(reply: str) -> list[dict[str, Any]]:
    """Extract the JSON payload from a model reply, tolerating code fences."""
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        return json.loads(text[start : end + 1]).get("candidates", [])
    except json.JSONDecodeError:
        return []
