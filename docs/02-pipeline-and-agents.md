# 02 — Pipeline and agents

## 1. The stage graph

Thirteen stages form a directed acyclic graph. Each stage reads approved artifacts of the stages it depends on and writes one versioned artifact.

| # | Stage key | Depends on | Main output | Engine (deterministic) | Agent work (LLM) |
|---|---|---|---|---|---|
| S1 | `intake` | — | `ProductSpec` | CAD extraction, unit normalisation, merge rules | Classify archetype, extract fields from text/PDF/photos, raise questions |
| S2 | `bom` | S1 | `Bom` (EBOM + MBOM), make/buy | Validation, EBOM→MBOM, cost/area preview | Draft BoM from archetype skeleton, enrich lines |
| S3 | `routing` | S2 | `Routing` + precedence graph + time standards | Catalog time models, DAG checks | Choose processes from catalog, sequence operations |
| S4 | `quality` | S3 | `QualityPlan` | AQL tables, inspector hours, FPY → rework | Select checks from catalog and compliance list |
| S5 | `capacity` | S3, S4, operating pattern | `CapacityDesign` (3 strategies) + chosen strategy | Takt, CP-SAT balancing, equipment sizing | Explain trade-offs, recommend a strategy |
| S6 | `inventory` | S2, S5, vendor lead times | `InventoryDesign` (zones, policies, flow matrix) | Safety stock, storage sizing, MHE counts | Classify storage types, flag hazards |
| S7 | `workorders_shifts` | S3, S5 | `WorkOrderDesign`, `Roster` | Release sequence, headcount, compliance checks | Draft work instructions per station |
| S8 | `utilities` | S5, S6, S7 | `UtilityDesign` | Load, MD, transformer, air, HVAC, water | Explain and flag unusual loads |
| S9 | `layout` | S4, S5, S6, S7, S8 | `Layout` (2D + 3D scene) | CP-SAT block layout, line layout, checks | Explain layout, propose adjacency overrides |
| S10 | `simulation` | S5, S7, S9 | `SimResult` + recommendations | SimPy model, replications, KPIs | Interpret KPIs, propose fixes as change sets |
| S11 | `finance` | S2, S5–S9, vendors | `FinanceModel` | Capex, opex, P&L, NPV/IRR, sensitivity | Narrative summary |
| S12 | `reports` | all | `ReportPack` | Rendering, exports, SysML v2 | Executive summary text |
| P1 | `vendors` | S2 (parallel) | `VendorReport` | Scoring, dedupe | Search, read pages, extract facts with sources |

Custom workcenters are not a stage: the user can add them in S5, and everything downstream recalculates.

The operating pattern (shifts per day, days per week, target OEE) is captured at the start of S5. S7 builds rosters on top of it; changing the pattern marks S5 stale (ADR-013).

## 2. Stage run lifecycle

```
            ┌──────────── re-run requested ───────────┐
            ▼                                          │
 pending → running → awaiting_approval → approved ─────┤
             │             │                │          │
             ▼             ▼                ▼          │
           failed       rejected          stale ───────┘
```

- `StageRun(project, stage, version, status, inputs_hash, artifact, created_at)`.
- `inputs_hash` = hash of the approved upstream artifact versions + catalog version + data-pack version + stage parameters. If an upstream artifact is re-approved with a different version, every downstream run whose hash no longer matches becomes `stale`.
- A stale stage keeps its last artifact visible (greyed) until re-run.
- Approval is explicit. The user can approve, edit then approve, or reject with a note (the agent re-runs with the note as guidance).
- Each artifact version is immutable. Exports always reference artifact versions.

## 3. Harness (LangGraph)

- One parent graph per project with a node per stage; each stage node is a subgraph.
- Postgres checkpointer stores graph state so a run survives restarts and can pause indefinitely for approval (`interrupt`).
- **Graph state holds IDs, not data.** Artifacts live in domain tables (`stage_run.artifact` JSONB with Pydantic validation). The graph can change without migrating stored projects (ADR-003).
- Long jobs (CAD extraction, CP-SAT, simulation) run in a worker process (`arq` + Redis or a Postgres-backed queue; P0 decides) so the API stays responsive. Progress events stream to the browser over SSE.
- Concurrency: at most 2 pipelines executing at once on the POC server; further runs queue (doc 11).

### Stage subgraph (same shape for every stage)

```
load_inputs → plan → tool_loop ⇄ tools → draft (structured output)
           → validate ── fail (≤2 repairs) ──▶ repair → validate
                       └ pass ─▶ compute (engines) → summarise → interrupt(approval)
```

1. **load_inputs** — fetch approved upstream artifacts, catalog and data-pack versions.
2. **plan** — short LLM step listing what must be decided (skipped for engine-only stages).
3. **tool_loop** — the agent calls tools: catalog search, data-pack lookup, calculators, web search (vendors only).
4. **draft** — the agent returns a Pydantic model via structured output (JSON schema enforced).
5. **validate** — schema + domain validators (doc 04 §5). Errors go back to the agent with exact messages; at most two repairs, then the stage fails with the errors shown to the user.
6. **compute** — engines produce all numbers. Agents never overwrite engine numbers.
7. **summarise** — the agent writes a short explanation and a list of questions/assumptions.
8. **interrupt** — wait for the user.

### Tools available to agents

| Tool | Stages | Returns |
|---|---|---|
| `catalog.search(query, kind)` | S2–S8 | Matching processes, machine classes, checks (vector + keyword) |
| `catalog.get(code)` | S2–S8 | Full entry |
| `pack.get(key, state)` | S4–S11 | Data-pack value with effective date and source |
| `calc.*` | all | Engine calculators (takt, AQL sample size, safety stock…) for explanation |
| `bom.skeleton(archetype)` | S2 | Archetype starting BoM |
| `web.search`, `web.fetch` | vendors, compliance research | Results and cleaned page text with URL |
| `ask_user(question, options)` | all | Adds a question to the approval screen (non-blocking during the run) |

## 4. LLM router

- LiteLLM with named model roles, not hard-coded models:

| Role | Default (POC) | Fallback | Used for |
|---|---|---|---|
| `reasoner` | Groq large open model (e.g. a 70–120B class model) | Gemini free tier | Planning, BoM drafting, routing, recommendations |
| `extractor` | Groq small fast model | `reasoner` | Field extraction, classification, page facts |
| `vision` | Gemini free tier | none (photo intake skipped with a notice) | Photos |
| `writer` | Groq large model | `reasoner` | Summaries, work instructions, report text |

- Model names live in config, so a paid model can replace any role later without code changes.
- Free-tier limits are per model and per organisation (doc 11 §5). The router enforces per-role RPM/TPM budgets with a token bucket, retries 429s with backoff, and degrades to the fallback role.
- **Token budget per stage** (configurable): e.g. intake 30k, BoM 25k, routing 30k, quality 15k, capacity 8k, others ≤ 10k, vendors 60k. A stage that would exceed its budget stops and asks the user to continue.
- Prompts keep context small: agents receive IDs and summaries, and fetch details by tool. Engines do the arithmetic, which removes the largest token sink.
- Every LLM call is logged (role, model, tokens, latency, stage run) for cost and quota tracking.

## 5. Questions and approvals UI

- Each stage screen shows: artifact (tables/graphs), engine numbers, provenance badges, assumptions, open questions, and actions (Approve · Edit · Re-run with note · Reject).
- Questions are typed (choice, number with unit, text, file) and answering one can trigger a partial re-run.
- The pipeline view (React Flow) shows every stage with status colours and stale markers; clicking a stage opens it.
- Edits by the user set `source = user`, confidence 1.0, and are never overwritten by a re-run unless the user clears them.

## 6. Change sets (feedback from simulation and layout)

Simulation and layout recommendations are expressed as **change sets**: a list of typed patches to a named upstream artifact (e.g. `capacity: add parallel station to WC-TEST-FUNC`, `capacity: buffer B3 capacity 4→8`, `layout: pin zone Z-STORE-LI near dock`). Applying a change set creates a new version of that artifact, marks downstream stale, and offers "re-run from here". The user sees the diff before applying.

## 7. Failure handling

| Failure | Behaviour |
|---|---|
| LLM quota exhausted | Stage pauses with "resume later" and an estimate; optional user-supplied Groq key field |
| Validation fails after repairs | Stage `failed`, errors listed, user can edit the draft directly |
| Solver timeout | Heuristic fallback result, marked `fallback` |
| Vision unavailable | Photo intake skipped with a notice; other inputs continue |
| Web search fails | Vendor report partial, with failed queries listed |
| Worker crash | Job retried once; checkpoint resumes the graph |
