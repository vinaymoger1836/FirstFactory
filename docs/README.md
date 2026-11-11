# Factory Layout Platform — architecture package

An agentic platform that takes a **product template** from an entrepreneur and produces a **complete factory design**: BoM, process routing, quality plan, line design, inventory and warehouse, work orders and shifts, utilities, an interactive **3D factory layout**, a production **simulation**, **financials**, a **vendor report**, and a **compliance checklist**. It replaces the first months of work with consultants for someone who has a product design and needs to build the factory for it.

This package is design only. Implementation happens in Claude Code, one phase at a time (doc 14).

## Status

| Item | Value |
|---|---|
| Stage | Architecture complete, ready for P0 |
| Scope | POC on free tools; simple electronics and mechanical products; India (Karnataka first) |
| Golden products | Power bank (10,000 / 20,000 mAh) and BLDC ceiling fan (1200 / 900 mm) |
| Standard | SysML v2 (exported from the typed domain model) |
| Date | 2026-11-11 |

## Documents

| # | Doc | What it covers |
|---|---|---|
| 01 | [Overview](01-overview.md) | Problem, users, scope, principles, system context, tech stack |
| 02 | [Pipeline and agents](02-pipeline-and-agents.md) | The 13-stage DAG, LangGraph harness, agent loop, approval gates, staleness, LLM router |
| 03 | [Product template, intake and BoM](03-product-template-intake-bom.md) | Template schema, web wizard, file intake (STEP/STL/photo/PDF), BoM generation, make/buy |
| 04 | [Data model and SysML v2](04-data-model-and-sysml.md) | Entities, provenance, versioned stage runs, SysML v2 mapping |
| 05 | [Routing, quality and capacity](05-routing-quality-capacity.md) | Time standards, quality plan, takt, line balancing, three mixed-model strategies |
| 06 | [Inventory, work orders, shifts, utilities](06-inventory-workorders-shifts-utilities.md) | Stock policy, storage sizing, WO design, rosters and compliance, electrical/air/HVAC/water/fire |
| 07 | [Layout and 3D](07-layout-and-3d.md) | Block layout solver, line layout, greenfield vs existing shed, 3D viewer and editing |
| 08 | [Simulation](08-simulation.md) | SimPy model generation, experiments, KPIs, bottlenecks, feedback loop |
| 09 | [Finance, vendors, reports](09-finance-vendors-reports.md) | Capex/opex, project financials, vendor research rules, report set and exports |
| 10 | [Reference library and data packs](10-reference-library-and-data-packs.md) | Process catalog schema, Karnataka data pack, admin page |
| 11 | [Hosting, security, operations](11-hosting-security-ops.md) | Oracle Always Free deployment, no-login abuse controls, retention, LLM quotas |
| 12 | [ADRs](12-adrs.md) | Every decision with alternatives and consequences |
| 13 | [Roadmap and risks](13-roadmap-and-risks.md) | Phases, risk register, open items |
| 14 | [Claude Code hand-off](14-claude-code-handoff.md) | Repo layout, phase prompts, acceptance tests, golden cases |
| — | [CLAUDE.md](CLAUDE.md) | Rules for Claude Code, copied to the repo root |

An interactive architecture page (clickable pipeline, data model, deployment, phase plan and calculators) accompanies this package as a published artifact.

## Decision log (summary)

| Area | Decision |
|---|---|
| Core rule | LLM proposes, deterministic engines calculate, user approves (ADR-001) |
| Model of record | Pydantic domain model; SysML v2 is a validated export (ADR-002) |
| Orchestration | LangGraph; domain tables are the system of record (ADR-003) |
| LLMs | LiteLLM router: Groq free tier for text, Gemini free tier for vision (ADR-004) |
| Pipeline | 13-stage DAG with versioned stage runs and stale propagation (ADR-005) |
| Order | Quality plan before capacity; operating pattern is a capacity input (ADR-013) |
| Variants | Two variants per product; mixed-model, dedicated and batch strategies computed side by side (ADR-014) |
| Solvers | OR-Tools CP-SAT for line balancing and layout, with heuristic fallbacks (ADR-006) |
| Simulation | SimPy discrete-event simulation with replications (ADR-007) |
| 3D | React Three Fiber scene generated from a 2D block layout (ADR-008) |
| Knowledge | Versioned process catalog (YAML + admin page); projects pin a version (ADR-009) |
| Regional data | Karnataka data pack with effective dates and sources (ADR-010) |
| Template input | Web wizard only for the POC (ADR-015) |
| Hosting | Oracle Always Free Ampere A1 (2 OCPU / 12 GB) + Cloudflare Pages (ADR-011) |
| Access | No login: capability links, Turnstile, rate limits, 30-day retention with export (ADR-012) |
| Fan | BLDC; controller PCBA bought in |
| Power bank | PCBA and cells bought in; grading, welding, assembly and test in-house |
| Test policy | Power bank: 100% short functional test, sampled capacity test, with a 100%-capacity toggle |
| Defaults | 1 shift, 6 days/week, 85% OEE; 5-year horizon, D:E 70:30, 12% discount rate; 20 days × 10 replications |
