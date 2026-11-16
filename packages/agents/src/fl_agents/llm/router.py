"""LLM router: named roles, budgets, 429 backoff, fallbacks, logging, cassettes (docs/02 §4).

Agents call ``Router.structured(role, messages, OutModel, ctx)``. The router:

1. checks the stage run's token budget (raises ``BudgetExhausted`` → the stage pauses);
2. in replay mode answers from a cassette (no network, no quota);
3. otherwise waits on the role's token bucket, calls LiteLLM, retries 429s honouring
   ``retry-after`` with jittered backoff, then falls back to the role's fallback role;
   a quota that stays exhausted raises ``QuotaExhausted`` (→ pause, "resume later");
4. logs every call to ``llm_call`` and adds the tokens to the run;
5. validates the output against the Pydantic model (``LlmOutputInvalid`` on mismatch, so
   the stage's repair loop can react).

No provider SDK is called directly (CLAUDE.md rule 7); model names come from settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError

from fl_agents.llm.cassette import CassetteStore, request_key
from fl_agents.llm.errors import (
    BudgetExhausted,
    CassetteMiss,
    LlmOutputInvalid,
    LlmUnavailable,
    QuotaExhausted,
)
from fl_agents.settings import Settings
from fl_agents.store.repo import Store

__all__ = ["ROLES", "CallCtx", "Role", "Router", "TokenBucket"]

log = logging.getLogger(__name__)

Role = Literal["reasoner", "extractor", "writer", "vision"]
T = TypeVar("T", bound=BaseModel)

# Fallback chain (docs/02 §4). Vision has none: photo intake is skipped with a notice.
ROLES: dict[str, str | None] = {
    "reasoner": "extractor",
    "extractor": "reasoner",
    "writer": "reasoner",
    "vision": None,
}
# How structured output is requested per role. Small Groq models accept JSON mode but not
# JSON-schema mode; the schema is always also given in the prompt and validated after.
_RESPONSE_MODE: dict[str, str] = {
    "reasoner": "json_schema",
    "extractor": "json_object",
    "writer": "json_schema",
    "vision": "json_object",
}
_CHARS_PER_TOKEN = 4  # estimate only; the provider's usage figure replaces it after the call
_IMAGE_TOKEN_ESTIMATE = 800


@dataclass
class CallCtx:
    """Who a call is for: used for budget checks and the llm_call log."""

    project_id: UUID | None = None
    run_id: UUID | None = None
    budget: int = 0  # 0 = no stage budget (e.g. wizard column mapping)


@dataclass
class TokenBucket:
    """Async RPM + TPM bucket with continuous refill (P0 spike c)."""

    rpm: int
    tpm: int
    _req: float = field(init=False)
    _tok: float = field(init=False)
    _t: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self._req, self._tok, self._t = float(self.rpm), float(self.tpm), time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        self._req = min(self.rpm, self._req + dt * self.rpm / 60)
        self._tok = min(self.tpm, self._tok + dt * self.tpm / 60)

    async def acquire(self, est_tokens: int) -> float:
        est = min(est_tokens, self.tpm)
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._req >= 1 and self._tok >= est:
                    self._req -= 1
                    self._tok -= est
                    return waited
                need_r = (1 - self._req) * 60 / self.rpm if self._req < 1 else 0.0
                need_t = (est - self._tok) * 60 / self.tpm if self._tok < est else 0.0
                wait = max(need_r, need_t, 0.05)
                waited += wait
                await asyncio.sleep(wait)

    def settle(self, est: int, actual: int) -> None:
        self._tok = max(-self.tpm, min(self.tpm, self._tok + est - actual))


def _schema_prompt(out: type[BaseModel]) -> str:
    schema = json.dumps(out.model_json_schema(), separators=(",", ":"))
    return (
        "Reply with one JSON object only, no prose and no code fences. "
        f"It must match this JSON Schema: {schema}"
    )


def _estimate(messages: list[dict[str, Any]], out_tokens: int) -> int:
    chars = 0
    images = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    chars += len(str(p.get("text", "")))
                elif isinstance(p, dict) and p.get("type") == "image_url":
                    images += 1
    return chars // _CHARS_PER_TOKEN + images * _IMAGE_TOKEN_ESTIMATE + out_tokens


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse[M: BaseModel](role: str, content: str, out: type[M]) -> M:
    text = _FENCE.sub("", content or "").strip()
    try:
        return out.model_validate_json(text)
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:8]
        )
        raise LlmOutputInvalid(role, msgs) from e


def _retry_after(exc: Exception) -> float | None:
    headers: dict[str, Any] = {}
    h = getattr(exc, "headers", None)
    if isinstance(h, dict):
        headers.update(h)
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "headers", None) is not None:
        with contextlib.suppress(TypeError, ValueError):
            headers.update(dict(resp.headers))
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


class Router:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.cassettes = CassetteStore(settings.cassette_dir)
        self._buckets: dict[str, TokenBucket] = {}

    @property
    def mode(self) -> str:
        return self.settings.llm_mode

    def available(self, role: str) -> bool:
        """Whether a role can answer at all (live/record: needs a provider key; replay:
        whatever was available when the cassettes were recorded)."""
        if self.mode == "replay":
            return not self.cassettes.is_unavailable(role)
        ok = bool(self.settings.provider_key(self.settings.role_model(role)))
        if not ok and self.mode == "record":
            self.cassettes.mark_unavailable(role)
        return ok

    def _bucket(self, role: str) -> TokenBucket:
        if role not in self._buckets:
            self._buckets[role] = TokenBucket(
                rpm=self.settings.role_rpm.get(role, 30), tpm=self.settings.role_tpm.get(role, 6000)
            )
        return self._buckets[role]

    async def _check_budget(self, ctx: CallCtx, est: int) -> None:
        if not ctx.budget or ctx.run_id is None:
            return
        run = await self.store.run(ctx.run_id)
        used = int(run["tokens_used"]) if run else 0
        budget = int(run["token_budget"]) if run and run["token_budget"] else ctx.budget
        if used + est > budget:
            raise BudgetExhausted(used, budget, est)

    async def _log(
        self,
        ctx: CallCtx,
        *,
        role: str,
        model: str,
        status: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        key: str | None = None,
        fallback_from: str | None = None,
        error: str | None = None,
    ) -> None:
        await self.store.log_llm_call(
            project_id=ctx.project_id,
            stage_run_id=ctx.run_id,
            role=role,
            model=model,
            fallback_from=fallback_from,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            cassette_key=key,
            error=error[:2000] if error else None,
        )
        if ctx.run_id is not None and (prompt_tokens or completion_tokens):
            await self.store.add_tokens(ctx.run_id, prompt_tokens + completion_tokens)

    async def structured(
        self,
        role: Role,
        messages: list[dict[str, Any]],
        out: type[T],
        ctx: CallCtx,
        *,
        out_tokens: int = 700,
        temperature: float = 0.0,
    ) -> T:
        """One structured call; see the module docstring for the steps."""
        full = [{"role": "system", "content": _schema_prompt(out)}, *messages]
        schema = out.model_json_schema()
        key = request_key(role, full, out.__name__, schema, temperature)
        est = _estimate(full, out_tokens)
        await self._check_budget(ctx, est)

        if self.mode == "replay":
            rec = self.cassettes.get(key)
            if rec is None:
                await self._log(
                    ctx, role=role, model="replay", status="error", key=key, error="cassette miss"
                )
                raise CassetteMiss(key, role)
            usage = rec.get("usage", {})
            await self._log(
                ctx,
                role=role,
                model=str(rec.get("model", "replay")),
                status="replay",
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                key=key,
            )
            return _parse(role, str(rec["response"]["content"]), out)

        content, _model = await self._call_with_fallback(
            role, full, out, ctx, key, est, temperature
        )
        return _parse(role, content, out)

    async def _call_with_fallback(
        self,
        role: str,
        messages: list[dict[str, Any]],
        out: type[BaseModel],
        ctx: CallCtx,
        key: str,
        est: int,
        temperature: float,
    ) -> tuple[str, str]:
        tried: list[str] = []
        current: str | None = role
        last_retry_after: float | None = None
        last_busy = False
        while current is not None and current not in tried:
            tried.append(current)
            if not self.available(current):
                current = ROLES.get(current)
                continue
            try:
                return await self._call_role(
                    current,
                    messages,
                    out,
                    ctx,
                    key,
                    est,
                    temperature,
                    fallback_from=role if current != role else None,
                )
            except QuotaExhausted as e:
                last_retry_after = e.retry_after_s
                last_busy = e.busy
                current = ROLES.get(current)
        if not any(self.available(r) for r in tried):
            raise LlmUnavailable(
                f"no API key configured for role '{role}' or its fallback "
                f"({self.settings.role_model(role)})"
            )
        raise QuotaExhausted(role, last_retry_after, busy=last_busy)

    async def _call_role(
        self,
        role: str,
        messages: list[dict[str, Any]],
        out: type[BaseModel],
        ctx: CallCtx,
        key: str,
        est: int,
        temperature: float,
        *,
        fallback_from: str | None,
    ) -> tuple[str, str]:
        import litellm  # heavy import, deferred until a live call is needed
        from litellm import exceptions as lx

        model = self.settings.role_model(role)
        mode = _RESPONSE_MODE.get(role, "json_object")
        if mode == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": out.__name__,
                    "schema": out.model_json_schema(),
                    "strict": False,
                },
            }
        else:
            response_format = {"type": "json_object"}
        bucket = self._bucket(role)
        retry_after: float | None = None
        busy = False  # last failure was a timeout/5xx rather than a 429
        for attempt in range(self.settings.llm_max_retries):
            await bucket.acquire(est)
            t0 = time.monotonic()
            try:
                resp = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    temperature=temperature,
                    api_key=self.settings.provider_key(model),
                    timeout=self.settings.llm_timeout_s,
                    drop_params=True,
                )
            except lx.RateLimitError as e:
                busy = False
                retry_after = _retry_after(e)
                await self._log(
                    ctx,
                    role=role,
                    model=model,
                    status="rate_limited",
                    key=key,
                    fallback_from=fallback_from,
                    error=str(e),
                )
                delay = retry_after if retry_after is not None else 2.0 * 2**attempt
                delay = min(delay, 60.0) * (1 + random.random() * 0.2)  # noqa: S311
                log.warning("429 from %s (%s); retry in %.1fs", model, role, delay)
                await asyncio.sleep(delay)
                continue
            except (
                lx.APIConnectionError,
                lx.Timeout,
                lx.InternalServerError,
                lx.ServiceUnavailableError,
            ) as e:
                await self._log(
                    ctx,
                    role=role,
                    model=model,
                    status="error",
                    key=key,
                    fallback_from=fallback_from,
                    error=str(e),
                )
                busy = True
                await asyncio.sleep(1.0 * 2**attempt)
                continue
            except Exception as e:  # bad request, auth, unknown model, … (not retryable)
                await self._log(
                    ctx,
                    role=role,
                    model=model,
                    status="error",
                    key=key,
                    fallback_from=fallback_from,
                    error=str(e),
                )
                raise LlmUnavailable(f"{role} ({model}) failed: {e}") from e
            latency = int((time.monotonic() - t0) * 1000)
            content = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
            bucket.settle(est, pt + ct)
            await self._log(
                ctx,
                role=role,
                model=model,
                status="record" if self.mode == "record" else "ok",
                prompt_tokens=pt,
                completion_tokens=ct,
                latency_ms=latency,
                key=key,
                fallback_from=fallback_from,
            )
            if self.mode == "record":
                self.cassettes.put(
                    key,
                    role=role,
                    model=model,
                    schema_name=out.__name__,
                    messages=messages,
                    content=content,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                )
            return content, model
        raise QuotaExhausted(role, retry_after, busy=busy)
