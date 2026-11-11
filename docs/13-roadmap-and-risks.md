# 13 — Roadmap and risks

## 1. Phases

| Phase | Name | Outcome | Depends on |
|---|---|---|---|
| P0 | Foundations and spikes | Monorepo, Docker Compose (multi-arch), FastAPI + React shells, Postgres, CI; spikes: OCP/OR-Tools/WeasyPrint on ARM64, LiteLLM → Groq + Gemini, `sysml2kit` vs own serializer | — |
| P1 | Domain model, library, data pack | `packages/domain` with all artifact models + JSON Schema → TS; catalog seed (both archetypes); Karnataka pack with sources; golden fixtures; catalog/pack tests | P0 |
| P2 | Harness, wizard, intake | LangGraph pipeline skeleton with stage runs, approvals, staleness, SSE, LLM router with budgets; web wizard; S1 intake (text, PDF, STEP, photo); pipeline UI | P1 |
| P3 | BoM, routing, quality | S2 with make/buy preview, S3 with time standards and precedence, S4 quality plan; vendor research agent (parallel) | P2 |
| P4 | Capacity and inventory | S5 engine (takt, sizing rules, CP-SAT, strategies A/B/C, custom workcenters); S6 inventory and flow matrix | P3 |
| P5 | Work orders, shifts, utilities | S7 WO design, sequence, roster, compliance, welfare; S8 utilities | P4 |
| P6 | Layout and 3D | S9 block + line layout, greenfield/shed modes, 2D SVG, DXF, R3F viewer, editing with checks | P5 |
| P7 | Simulation | S10 model generator, experiments, KPIs, recommendations as change sets | P6 |
| P8 | Finance, reports, SysML | S11 finance; S12 report pack (web, PDF, XLSX, DXF, SysML, ZIP); admin page | P7 |
| P9 | Production hardening | Oracle deploy, Cloudflare Pages/Turnstile/Access, rate limits, global budget, retention job, backups, load test with both golden products | P8 |

The admin page can move earlier (P4/P5) if catalog editing becomes a bottleneck.

## 2. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Groq free-tier limits too low for multi-agent runs | High | High | Engines do arithmetic; small contexts; per-stage budgets; caching; queue; BYO key; paid role swap via config |
| R2 | Groq free models lack vision | Medium | Medium | `vision` role on Gemini free tier; photo intake optional |
| R3 | OCP/CadQuery or other deps fail on ARM64 | Medium | High | P0 spike; conda-forge; CAD worker could run on x86 elsewhere later |
| R4 | Oracle A1 capacity unavailable / account issues | Medium | High | Retry regions; keep x86 Docker Compose working for a cheap VPS fallback |
| R5 | Catalog estimates (times, prices, kW) inaccurate | High | High | Provenance + confidence; ranges; assumptions register; admin edits; user overrides; golden products reviewed by a manufacturing engineer |
| R6 | Regulatory values outdated | Medium | Medium | Dated pack with sources and `verify` flags; quarterly review; disclaimers |
| R7 | LLM invents vendors or facts | Medium | High | Evidence-required extraction; drop unsourced records; tests with fake pages |
| R8 | Solver too slow on 2 cores for larger products | Medium | Medium | Time limits; fallbacks; warm starts; problem size limits for POC |
| R9 | Simulation too slow | Medium | Medium | Aggregated events; fewer replications under load; worker queue |
| R10 | Abuse of open endpoints (no login) | Medium | Medium | Turnstile, rate limits, global budget, upload sandboxing |
| R11 | Users treat outputs as certified engineering/legal advice | Medium | High | Grade labels per output; disclaimers; "verify with" notes per section |
| R12 | SysML v2 libraries immature | Medium | Low | Own serializer for a defined subset; validator in CI |
| R13 | Scope creep (more products, states) | High | Medium | Archetype + pack structure makes additions data work; POC limited to two golden products |
| R14 | Mixed-model sequencing effects missed by static balance | Medium | Medium | Per-variant overload limit + simulation check |

## 3. Open items (decide during implementation)

- Worker queue technology (`arq` + Redis vs Postgres-backed) — P0.
- Exact Groq model names per role and budgets — P0 from the console.
- BESCOM LT/HT threshold and current Karnataka minimum wages — P1 data pack, from primary sources.
- Free GLB asset set for machine classes and its licence — P6.
- Later: Excel template import; more states; accounts and sharing; 3D replay of simulation; paid LLM option.
