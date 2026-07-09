"""The validator-owned metered inference proxy (8.1).

V1 competes on the harness, not on model access. Validators supply a pinned code
model and a metered endpoint; miners compete on decomposition, hotspot selection,
candidate search, self-critique and rollback discipline under a fixed budget.

The proxy is the thing that makes "a fixed budget" true rather than requested
politely. It binds to loopback, it is reachable only from the artifact's network
namespace, it counts every token, and it stops answering when the budget is spent.

Deliberately narrow: an allow-list of fields, one pinned model, no streaming
passthrough of arbitrary provider parameters. Anything the artifact can smuggle
through this proxy is a hole in the budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

#: Only these fields survive the trip upstream. Everything else is dropped, so a
#: miner cannot reach provider-specific behaviour the budget does not model.
CHAT_ALLOWED_FIELDS = frozenset(
    {
        "messages",
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "tools",
        "tool_choice",
        "response_format",
        "seed",
    }
)


class BudgetExceeded(Exception):
    pass


@dataclass(slots=True)
class TokenLedger:
    """Per-run token accounting.

    The validator's count is authoritative. The agent's ``budget_used`` in
    report.json is informational and is cross-checked against this.
    """

    budget: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0
    first_request_at: float | None = None
    last_request_at: float | None = None

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.total)

    def reserve(self, estimated: int) -> None:
        if self.total + estimated > self.budget:
            raise BudgetExceeded(
                f"request needs ~{estimated} tokens, {self.remaining} remain of {self.budget}"
            )

    def record(self, prompt: int, completion: int) -> None:
        now = time.time()
        if self.first_request_at is None:
            self.first_request_at = now
        self.last_request_at = now
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.requests += 1


@dataclass(slots=True)
class ProxyConfig:
    """One pinned model, one upstream, one budget per run."""

    upstream_url: str
    upstream_api_key: str
    pinned_model: str
    #: run_id -> ledger. A request without a known run id is refused.
    ledgers: dict[str, TokenLedger] = field(default_factory=dict)
    #: Deterministic replay: the same seed produces the same sampling where the
    #: provider honours it, which is what makes a replay audit meaningful.
    inference_seed: int | None = None
    request_timeout_seconds: float = 120.0
    #: Rough character-per-token ratio used only for pre-flight reservation.
    chars_per_token: float = 4.0


class ChatRequest(BaseModel):
    model_config = {"extra": "allow"}


def create_app(config: ProxyConfig) -> FastAPI:
    """Build the proxy application.

    Bound to loopback by the caller. The artifact reaches it through a network
    namespace with exactly one route; there is no other egress.
    """
    app = FastAPI(title="CompilerForge inference proxy", docs_url=None, redoc_url=None)
    client = httpx.AsyncClient(timeout=config.request_timeout_seconds)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> dict:
        run_id = request.headers.get("x-cf-run-id", "")
        ledger = config.ledgers.get(run_id)
        if ledger is None:
            # No ledger means no budget means no inference. Fail closed.
            raise HTTPException(status_code=403, detail="unknown or expired run id")

        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="body must be JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        sanitized = {k: v for k, v in body.items() if k in CHAT_ALLOWED_FIELDS}
        # The model is pinned by the validator. A miner asking for a different one
        # is asking to compete on model access, which V1 does not reward.
        sanitized["model"] = config.pinned_model
        sanitized["stream"] = False
        if config.inference_seed is not None:
            sanitized["seed"] = config.inference_seed

        estimated = _estimate_prompt_tokens(sanitized, config.chars_per_token)
        estimated += int(sanitized.get("max_tokens", 1024) or 1024)
        try:
            ledger.reserve(estimated)
        except BudgetExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

        try:
            response = await client.post(
                config.upstream_url,
                json=sanitized,
                headers={"Authorization": f"Bearer {config.upstream_api_key}"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text[:500])

        payload = response.json()
        usage = payload.get("usage") or {}
        ledger.record(
            prompt=int(usage.get("prompt_tokens", 0)),
            completion=int(usage.get("completion_tokens", 0)),
        )
        # Tell the agent what it has left, so a well-behaved harness can plan its
        # search rather than discovering the wall by hitting it.
        payload["cf_budget"] = {
            "used": ledger.total,
            "remaining": ledger.remaining,
            "requests": ledger.requests,
        }
        return payload

    @app.get("/v1/budget")
    async def budget(request: Request) -> dict:
        run_id = request.headers.get("x-cf-run-id", "")
        ledger = config.ledgers.get(run_id)
        if ledger is None:
            raise HTTPException(status_code=403, detail="unknown or expired run id")
        return {"used": ledger.total, "remaining": ledger.remaining, "budget": ledger.budget}

    @app.on_event("shutdown")
    async def _close() -> None:
        await client.aclose()

    return app


def _estimate_prompt_tokens(body: dict, chars_per_token: float) -> int:
    chars = 0
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
    return int(chars / max(chars_per_token, 1.0))
