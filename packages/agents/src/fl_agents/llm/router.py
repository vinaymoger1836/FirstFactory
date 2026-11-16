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
from collections.abc import Awaitable, Callable
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
    ToolBudgetExhausted,
)
from fl_agents.settings import Settings
from fl_agents.store.repo import Store

__all__ = ["ROLES", "CallCtx", "Role", "Router", "TokenBucket", "Tool"]

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
class Tool:
    """A function the model may call during ``tool_loop``. Results are plain JSON."""

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[Any]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


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

        def request(r: str) -> dict[str, Any]:
            if _RESPONSE_MODE.get(r, "json_object") == "json_schema":
                return {
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": out.__name__, "schema": schema, "strict": False},
                    }
                }
            return {"response_format": {"type": "json_object"}}

        msg, _model = await self._call_with_fallback(
            role, full, request, out.__name__, ctx, key, est, temperature
        )
        return _parse(role, str(msg.get("content") or ""), out)

    async def tool_loop(
        self,
        role: Role,
        transcript: list[dict[str, Any]],
        tools: list[Tool],
        submit: type[T],
        ctx: CallCtx,
        *,
        max_tool_calls: int,
        save: Callable[[list[dict[str, Any]]], Awaitable[None]],
        on_tool: Callable[[str, dict[str, Any], str], Awaitable[None]] | None = None,
        temperature: float = 0.0,
        max_repairs: int = 2,
    ) -> T:
        """Tool-calling loop (docs/02 §3 tool_loop) ending in a ``submit_draft`` call.

        ``transcript`` holds the messages so far and is saved after every turn, so a pause
        (budget, tool-call limit, quota) resumes where it stopped without repeating a call.
        Each turn is one cassette in record/replay. Tool calls beyond ``max_tool_calls``
        raise ``ToolBudgetExhausted`` (the stage pauses). Invalid ``submit_draft``
        arguments are returned to the model as an error (at most ``max_repairs`` times).
        """
        submit_tool = {
            "type": "function",
            "function": {
                "name": "submit_draft",
                "description": "Submit your final draft. Call exactly once, when done.",
                "parameters": submit.model_json_schema(),
            },
        }
        schemas = [t.schema() for t in tools] + [submit_tool]
        by_name = {t.name: t for t in tools}
        msgs = list(transcript)
        repairs = sum(
            1 for m in msgs if m.get("role") == "tool" and m.get("name") == "submit_draft"
        )
        for _turn in range(max_tool_calls + max_repairs + 4):
            used = sum(
                1
                for m in msgs
                if m.get("role") == "tool" and m.get("name") not in (None, "submit_draft")
            )
            if used >= max_tool_calls:
                raise ToolBudgetExhausted(used, max_tool_calls)
            key = request_key(
                role, msgs, "tools:" + submit.__name__, {"tools": schemas}, temperature
            )
            est = _estimate(msgs, 900)
            await self._check_budget(ctx, est)
            if self.mode == "replay":
                rec = self.cassettes.get(key)
                if rec is None:
                    await self._log(
                        ctx,
                        role=role,
                        model="replay",
                        status="error",
                        key=key,
                        error="cassette miss",
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
                msg = dict(
                    rec["response"].get("message")
                    or {"role": "assistant", "content": rec["response"].get("content", "")}
                )
            else:
                send = [
                    {k: v for k, v in m.items() if not (m.get("role") == "tool" and k == "name")}
                    for m in msgs
                ]
                msg, _model = await self._call_with_fallback(
                    role,
                    send,
                    lambda _r: {"tools": schemas, "tool_choice": "auto"},
                    "tools:" + submit.__name__,
                    ctx,
                    key,
                    est,
                    temperature,
                )
            msgs.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                content = str(msg.get("content") or "")
                try:
                    return _parse(role, content, submit)
                except LlmOutputInvalid:
                    msgs.append(
                        {"role": "user", "content": "Call submit_draft with your draft now."}
                    )
                    await save(msgs)
                    continue
            result: T | None = None
            for call in calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                if name == "submit_draft":
                    try:
                        result = submit.model_validate(args)
                        reply = "accepted"
                    except ValidationError as e:
                        repairs += 1
                        reply = "Invalid draft: " + "; ".join(
                            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
                            for err in e.errors()[:8]
                        )
                        if repairs > max_repairs:
                            raise LlmOutputInvalid(role, reply) from e
                else:
                    tool = by_name.get(name)
                    try:
                        out = await tool.run(args) if tool else {"error": f"unknown tool {name}"}
                    except Exception as e:  # a tool error goes back to the model
                        out = {"error": f"{type(e).__name__}: {e}"}
                    reply = json.dumps(out, default=str, ensure_ascii=False)[:4000]
                    if on_tool is not None:
                        await on_tool(name, args, reply[:300])
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": name,
                        "content": reply,
                    }
                )
            await save(msgs)
            if result is not None:
                return result
        raise LlmOutputInvalid(role, "the model did not submit a draft")

    async def _call_with_fallback(
        self,
        role: str,
        messages: list[dict[str, Any]],
        request: Callable[[str], dict[str, Any]],
        schema_name: str,
        ctx: CallCtx,
        key: str,
        est: int,
        temperature: float,
    ) -> tuple[dict[str, Any], str]:
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
                    request(current),
                    schema_name,
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
        request: dict[str, Any],
        schema_name: str,
        ctx: CallCtx,
        key: str,
        est: int,
        temperature: float,
        *,
        fallback_from: str | None,
    ) -> tuple[dict[str, Any], str]:
        import litellm  # heavy import, deferred until a live call is needed
        from litellm import exceptions as lx

        model = self.settings.role_model(role)
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
                    temperature=temperature,
                    **request,
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
            raw = resp.choices[0].message
            content = raw.content or ""
            msg: dict[str, Any] = {"role": "assistant", "content": content}
            tcs = getattr(raw, "tool_calls", None) or []
            if tcs:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tcs
                ]
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
                    schema_name=schema_name,
                    messages=messages,
                    content=content,
                    message=msg,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                )
            return msg, model
        raise QuotaExhausted(role, retry_after, busy=busy)
