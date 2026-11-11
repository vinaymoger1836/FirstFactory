# 11 — Hosting, security and operations

## 1. Deployment (POC)

```
                 Cloudflare (DNS, TLS, Turnstile, Access for /admin)
                  │                                   │
   Cloudflare Pages (React build)          api.<domain> → Oracle VM
                                                       │
            Oracle Always Free — Ampere A1, 2 OCPU / 12 GB RAM, ARM64, Ubuntu LTS
            ┌──────────────────── Docker Compose ─────────────────────┐
            │ caddy (reverse proxy, TLS origin cert)                  │
            │ api (FastAPI + LangGraph)          ~1.5 GB               │
            │ worker (engines: CP-SAT, SimPy)    ~3 GB, 1 job at a time│
            │ cad-worker (OCP, sandboxed)        ≤2 GB per job         │
            │ postgres 16 + pgvector             ~1.5 GB               │
            │ redis (queue, rate limits)         ~0.2 GB               │
            │ searxng (optional)                 ~0.3 GB               │
            └──────────────────────────────────────────────────────────┘
            Block volume: /data (projects, files, exports), nightly pg_dump
```

### 1.1 Oracle Always Free facts (as of 2026)

- The Always Free Ampere A1 allowance was reduced from 4 OCPU / 24 GB to **2 OCPU / 12 GB** effective 15 June 2026 (https://infoq.com/news/2026/07/oracle-cloud-free-tier-limits/).
- If an Always Free tenancy has more A1 capacity provisioned than allowed at trial end, **all** A1 instances are disabled and deleted after 30 days unless upgraded (https://docs.cloud.oracle.com/Content/FreeTier/freetier.htm). → Provision exactly 2 OCPU / 12 GB from day one.
- Signup requires card verification; regional A1 capacity can be unavailable — retry or pick another home region.
- ARM64: all images must be multi-arch or built for `linux/arm64`. **P0 spike:** confirm CadQuery/OCP, OR-Tools, WeasyPrint install on ARM64 (fallback: conda-forge builds).

### 1.2 Why not other free hosts

- Hugging Face Spaces: creating Docker/Gradio Spaces now needs a paid plan (https://huggingface.co/docs/hub/en/spaces-overview).
- Render free web services: ~512 MB RAM — too small for OCP + OR-Tools + SimPy.
- Managed Postgres free tiers (e.g. Neon 1 GB/project): possible later; POC keeps Postgres on the VM to avoid connection limits and cold starts.

## 2. Access model without logins (ADR-012)

| Control | Detail |
|---|---|
| Capability link | Project URL contains a 128-bit random token; only its hash is stored. Anyone with the link can view/edit that project. A "read-only share link" (separate token) is offered for investors. |
| Turnstile | Cloudflare Turnstile challenge before creating a project and before each pipeline run. |
| Rate limits | Per IP: 3 new projects/day, 20 stage runs/day; per project: 1 concurrent run. Global: max 2 concurrent pipelines; queue with position shown. |
| Global LLM budget | Daily token budget per model role, below provider limits; when reached, new runs queue until reset. Shown on a status banner. |
| Bring-your-own key | Optional Groq API key per project (stored encrypted, deleted with the project) to bypass the shared budget. |
| Uploads | Allowed types only (STEP/STP, STL, JPG/PNG/WEBP, PDF, CSV/XLSX); size limits (CAD 50 MB, images 10 MB each, max 20 files); magic-byte sniffing; images re-encoded; PDFs parsed in the worker; CAD parsed in `cad-worker` with no network, read-only FS except temp, CPU/memory/time limits. |
| Admin | `/admin` behind Cloudflare Access (email OTP, allowlisted emails) + API passcode header; admin actions audited. |
| Secrets | `.env` on the VM (root-only), never in the repo; Groq/Gemini/Tavily keys; Cloudflare origin certificate. |
| Retention | Projects expire **30 days** after last activity; a daily job deletes rows and files. Banner shows the expiry date; "Export project (ZIP)" always available. |
| Privacy | No personal data required. Product files may be confidential: stated on the start page; files are not shared with LLM providers beyond what each stage needs (CAD geometry is processed locally; only extracted text/fields go to LLMs; photos go to the vision provider). |

## 3. Web security basics

CORS allowlist (Pages domain only) · CSRF not applicable (no cookies; token in path + header) · strict CSP on the web app · input validation via Pydantic everywhere · SQL only through the ORM/parameterised queries · LLM output never executed; formulas evaluated by a safe expression evaluator (no `eval`) · SSRF guard on `web.fetch` (public IPs only, no redirects to private ranges, size/time limits) · dependency scanning in CI.

## 4. Observability

- Structured JSON logs (api, worker), request IDs, stage-run IDs.
- `llm_call` table → daily usage dashboard (tokens per role, 429 rate, latency).
- Health endpoints; Uptime monitoring with a free external pinger.
- Error alerts to email (free SMTP relay) for worker crashes and quota exhaustion.

## 5. LLM quotas (design inputs)

- Groq free tier: limits are per model and apply at the organisation level, so extra keys do not raise them (https://pricepertoken.com/endpoints/groq/free). One published example for a large model: 30 RPM, 1K requests/day, 8K tokens/min, 200K tokens/day (https://www.developersdigest.tech/models/free-api/groq). Exact limits are read from the Groq console and stored in config.
- Groq free models may not accept images (reported: https://aiscouncil-docs.pages.dev/providers/groq) → the `vision` role uses Gemini's free tier.
- Implication: at ~200K tokens/day for the large model, the shared budget supports only a few full projects per day. Engines doing the arithmetic, small contexts, caching (vendor cache, catalog summaries) and BYO keys are therefore required, not optional.

## 6. Backups and recovery

Nightly `pg_dump` + `/data` tarball to Oracle Object Storage (Always Free tier) with 7-day rotation; restore runbook tested in P9.

## 7. Environments

- `local`: Docker Compose on a developer machine (x86 or ARM).
- `prod`: the Oracle VM. Deploy = GitHub Actions builds multi-arch images → GHCR → SSH `docker compose pull && up -d` with a migration step.
- Feature flags in config for vision intake, vendor research and simulation (to shed load).
