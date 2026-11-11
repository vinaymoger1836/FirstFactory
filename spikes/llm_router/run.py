"""Spike c: LiteLLM roles + structured JSON output + token-bucket budget + real rate-limit headers.

Roles (docs/02 §4): reasoner, extractor, writer → Groq; vision → Gemini. Keys come from the
git-ignored .env (GROQ_API_KEY, GEMINI_API_KEY); model names from LLM_ROLE_* (see .env.example).

    uv run --no-project --exclude-newer 2026-10-28 --python 3.12 \
        --with litellm==<pinned> --with pillow python spikes/llm_router/run.py

Part 1 (token bucket) runs offline. Part 2 calls the providers only if real keys are present.
Raw responses are never written to disk; only header names/values for rate limits and usage.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "out"


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def real_key(name: str) -> bool:
    v = os.environ.get(name, "")
    return bool(v) and "placeholder" not in v


# ── Token bucket (per role: requests/min and tokens/min) ───────────
@dataclass
class TokenBucket:
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
        """Wait until one request and est_tokens are available. Returns seconds waited."""
        if est_tokens > self.tpm:
            raise ValueError("request larger than the per-minute token budget")
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._req >= 1 and self._tok >= est_tokens:
                    self._req -= 1
                    self._tok -= est_tokens
                    return waited
                need = max(
                    (1 - self._req) * 60 / self.rpm if self._req < 1 else 0,
                    (est_tokens - self._tok) * 60 / self.tpm if self._tok < est_tokens else 0,
                )
                await asyncio.sleep(need)
                waited += need

    def settle(self, est_tokens: int, actual_tokens: int) -> None:
        """Correct the estimate with real usage after the call."""
        self._tok = min(self.tpm, self._tok + est_tokens - actual_tokens)


async def bucket_test() -> dict:
    b = TokenBucket(rpm=6, tpm=3000)  # 1 request / 10 s steady state
    t0 = time.monotonic()
    waits = [round(await b.acquire(400), 2) for _ in range(8)]
    return {
        "config": {"rpm": 6, "tpm": 3000, "est_tokens": 400, "requests": 8},
        "waits_s": waits,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "expect": "first 6 immediate (burst = rpm), then ~10 s each (rpm-bound); tokens: 3000/400=7 fit",
    }


# ── Roles ──────────────────────────────────────────────────────────
ROLES = {
    "reasoner": {"model": "LLM_ROLE_REASONER", "fallback": "extractor", "rpm": 30, "tpm": 8000},
    "extractor": {"model": "LLM_ROLE_EXTRACTOR", "fallback": "reasoner", "rpm": 30, "tpm": 6000},
    "writer": {"model": "LLM_ROLE_WRITER", "fallback": "reasoner", "rpm": 30, "tpm": 8000},
    "vision": {"model": "LLM_ROLE_VISION", "fallback": None, "rpm": 10, "tpm": 250000},
}


class ProductFields(BaseModel):
    product_name: str
    capacity_mah: int | None = Field(description="Rated capacity in mAh, null if not stated")
    output_ports: list[str]
    annual_volume: int | None


class OperationList(BaseModel):
    operations: list[str] = Field(min_length=3, max_length=6)


class Summary(BaseModel):
    summary: str = Field(max_length=400)


class LabelReading(BaseModel):
    model_code: str | None
    capacity_mah: int | None


def schema_format(model: type[BaseModel]) -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": model.model_json_schema(),
            "strict": False,
        },
    }


RATE_HEADER_HINTS = ("ratelimit", "retry-after", "x-groq", "quota")


async def call(role: str, messages: list, out: type[BaseModel], buckets: dict) -> dict:
    import litellm

    cfg = ROLES[role]
    model = os.environ[cfg["model"]]
    est = 1500
    waited = await buckets[role].acquire(est)
    rec: dict = {"role": role, "model": model, "bucket_wait_s": round(waited, 2)}
    t = time.perf_counter()
    for attempt in range(3):
        try:
            resp = await litellm.acompletion(
                model=model, messages=messages, response_format=schema_format(out), temperature=0
            )
            break
        except litellm.RateLimitError as exc:
            rec.setdefault("429s", []).append(str(exc)[:160])
            await asyncio.sleep(2**attempt)
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
            return rec
    else:
        rec["error"] = "rate limited after retries"
        if cfg["fallback"]:
            rec["fallback"] = await call(cfg["fallback"], messages, out, buckets)
        return rec
    rec["latency_s"] = round(time.perf_counter() - t, 2)
    usage = resp.usage
    rec["usage"] = {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens}
    buckets[role].settle(est, usage.total_tokens)
    hdrs = (getattr(resp, "_hidden_params", {}) or {}).get("additional_headers", {}) or {}
    rec["rate_limit_headers"] = {
        k: v for k, v in sorted(hdrs.items()) if any(h in k.lower() for h in RATE_HEADER_HINTS)
    }
    text = resp.choices[0].message.content or ""
    try:
        rec["parsed"] = out.model_validate_json(text).model_dump()
        rec["schema_valid"] = True
    except Exception as exc:
        rec["schema_valid"] = False
        rec["schema_error"] = str(exc)[:200]
    return rec


def label_png() -> str:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (520, 200), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, 515, 195], outline="black", width=3)
    d.text((20, 30), "Model: PB10K", fill="black", font_size=36)
    d.text((20, 90), "Capacity: 10000 mAh  37 Wh", fill="black", font_size=30)
    d.text((20, 145), "Input 5V 2A  Output USB-C / USB-A", fill="black", font_size=22)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def provider_test() -> dict:
    buckets = {r: TokenBucket(c["rpm"], c["tpm"]) for r, c in ROLES.items()}
    res: dict = {}
    blurb = (
        "We want to make the PB10K, a 10,000 mAh power bank with one USB-C PD port and one "
        "USB-A port, about 1,80,000 units a year."
    )
    if real_key("GROQ_API_KEY"):
        res["extractor"] = await call(
            "extractor",
            [{"role": "user", "content": f"Extract the product fields as JSON.\n\n{blurb}"}],
            ProductFields,
            buckets,
        )
        res["reasoner"] = await call(
            "reasoner",
            [{"role": "user", "content": "List 3-6 assembly operations for a power bank. JSON."}],
            OperationList,
            buckets,
        )
        res["writer"] = await call(
            "writer",
            [{"role": "user", "content": f"Summarise in two sentences as JSON: {blurb}"}],
            Summary,
            buckets,
        )
    else:
        res["groq"] = "skipped: GROQ_API_KEY not set in .env"
    if real_key("GEMINI_API_KEY"):
        res["vision"] = await call(
            "vision",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read the product label. JSON."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{label_png()}"},
                        },
                    ],
                }
            ],
            LabelReading,
            buckets,
        )
    else:
        res["gemini"] = "skipped: GEMINI_API_KEY not set in .env"
    return res


async def main() -> None:
    from importlib.metadata import version

    load_env()
    OUT.mkdir(exist_ok=True)
    result = {
        "versions": {"litellm": version("litellm")},
        "token_bucket": await bucket_test(),
        "providers": await provider_test(),
    }
    print(json.dumps(result, indent=2))
    (OUT / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
