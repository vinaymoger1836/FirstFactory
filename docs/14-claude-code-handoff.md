# 14 — Claude Code hand-off

How to turn this design into code with Claude Code, one phase at a time. Each phase has a copy-paste prompt, an end-to-end acceptance check and a short review checklist for you.

Owner's working rules in `CLAUDE.md` apply to every phase: Claude Code never commits or pushes (you do), nothing sensitive or generated goes into git, no unit tests after small changes (one end-to-end check when needed, usually at phase end), and the UI follows the accessibility and light/dark theme rules.

## 1. One-time setup

**Install** (all free): Docker + Docker Compose (with buildx for multi-arch), Python 3.12 + `uv`, Node 22 + `pnpm`, git, GitHub CLI.

**Create the repo**

```bash
mkdir factory-layout-platform && cd factory-layout-platform && git init
mkdir -p docs
cp /path/to/this/package/*.md docs/          # README, 01–14
cp docs/CLAUDE.md ./CLAUDE.md                 # Claude Code reads it from the repo root
git add . && git commit -m "Architecture package"   # you commit; Claude Code never does
```

Create a GitHub repository; you push to it manually. GitHub Actions only builds the multi-arch Docker images. Get free API keys: Groq, Google AI Studio (Gemini), Tavily (optional). Keys live in `.env`, which is git-ignored and never committed.

## 2. Repository layout (target)

```
apps/
  web/                 React + Vite + TS (wizard, pipeline, stage screens, 3D, admin)
  api/                 FastAPI app, routes, SSE, auth-by-token, rate limits
  worker/              job runner for engines, CAD, simulation, reports
packages/
  domain/              Pydantic models, validators, JSON Schema export, provenance
  engines/             pure Python: capacity, quality_sizing, inventory, roster,
                       utilities, layout, simulation, finance, sysml (no LLM imports)
  agents/              LangGraph graphs, stage subgraphs, prompts, tools, LLM router
  ts-types/            generated TypeScript types (built at build time, git-ignored)
library/
  catalog/             YAML catalog seed
  packs/KA/            Karnataka data pack
golden/
  power_bank/          template input, files, expected outputs per stage
  ceiling_fan/
  benchmarks/          SALBP instances, tiny QAP, queueing cases
infra/                 docker-compose.yml, caddy, deploy scripts
docs/                  this package + phase-reports/ + versions.md
```

## 3. Working agreement for every phase

1. **Fresh Claude Code session per phase**; paste the phase prompt.
2. **Plan first** (plan mode). Review the plan before code; push back on anything outside the phase.
3. **No commits or pushes by Claude Code.** Changes stay in the working tree. At the end of the phase Claude Code lists what changed; you review, commit and push yourself (branching is your choice).
4. **Testing:** no unit tests after small pieces of code. One full end-to-end check when needed, normally at the end of the phase: the golden cases run through the real pipeline with recorded LLM responses (no quota used).
5. **Git hygiene:** `.gitignore` is created in P0 and updated in the same change whenever a new kind of secret, generated or build file appears. Check `git status` before you commit.
6. **Phase report** in `docs/phase-reports/P<n>.md`: what was built, files changed, deviations and why, measured numbers, known gaps.
7. **Design changes** only through a new ADR in `docs/12-adrs.md` with status *Proposed*; you approve.
8. **Versions** pinned in `docs/versions.md`; upgrades only between phases.
9. Golden expected outputs change only with an explanation in the phase report.

## 4. Golden cases (shared by all phases)

| Case | Inputs | Exact assertions |
|---|---|---|
| G1 Power bank | PB10K 60% / PB20K 40%; 3,00,000/yr; 300 days; 1 shift; 27,000 s net/day; OEE 0.85; buy PCBA + cells | Daily demand 1,000 (600/400); takt 27.0 s; PCT 22.95 s; functional test (60 s) positions = 3; sampled capacity test (2%, 6 h / 10 h, 22 rack-h/day, +15%) channels = 8; 100% capacity toggle channels = 398 |
| G2 BLDC fan | 1200 mm 70% / 900 mm 30%; 1,00,000/yr; same pattern; buy controller PCBA, rotor/shell, blades | Daily demand 333.3; takt 81.0 s; PCT 68.85 s; run-in test (300 s) positions = 5; varnish oven (60/batch, 2 h, 7.5 h × 0.85) ovens = 2 |
| G3 Utilities | MD 180 kVA | Transformer 250 kVA |
| G4 Finance | Cash flows [-100, 30×5] at 12% | NPV 8.14; IRR 15.24% |
| G5 Balancing | Scholl SALBP-1 instances (Jackson, Mertens, Bowman) | Documented optimal station counts |
| G6 Layout | 4–5 zone QAP | Equals brute-force optimum |
| G7 Simulation | Deterministic serial line; M/M/1 at ρ = 0.8 | Throughput = 1/max(cycle); mean L = 4 within CI |

These are the end-to-end checks. G1 and G2 run through the real pipeline with recorded LLM responses (cassettes), so results are repeatable and use no quota. Each phase's acceptance list says which golden checks apply at that point.

## 5. Phase prompts

### P0 — Foundations and spikes

```text
Phase 0 — Foundations and spikes for the Factory Layout Platform.
Read first: CLAUDE.md, docs/01-overview.md, docs/02-pipeline-and-agents.md §3–4, docs/11-hosting-security-ops.md, docs/12-adrs.md ADR-002, 003, 004, 011.
Plan first, then build:
1. Monorepo per docs/14 §2 with uv workspaces (Python) and pnpm (web). Lint/format/type-check config (ruff, mypy, eslint, tsc). A full .gitignore per CLAUDE.md owner's rule 1, and .env.example with placeholder keys only.
2. infra/docker-compose.yml: caddy, api, worker, cad-worker, postgres16+pgvector, redis. GitHub Actions workflow that only builds multi-arch images (linux/amd64, linux/arm64).
3. One end-to-end check command (e.g. `make e2e`) that will run the golden cases; in P0 it only brings the stack up and hits the health endpoints.
4. apps/api: FastAPI health, project create (capability token, hashed), SSE demo endpoint. apps/web: Vite React shell with routing, design tokens, light/dark theme switch (system default + manual toggle) and an accessible main menu (CLAUDE.md owner's rules 4–5).
5. Spikes, each with a short result section in the phase report:
   a. CadQuery/OCP: load a sample STEP on arm64 and amd64, list solids and bboxes, export GLB.
   b. OR-Tools CP-SAT and WeasyPrint import and run on arm64.
   c. LiteLLM roles (reasoner, extractor, writer → Groq; vision → Gemini) with structured JSON output and a token-bucket budget; record real rate-limit headers.
   d. SysML v2: write the doc 04 §6 example with our own serializer and with sysml2kit; parse both with sysmlpy; recommend one.
   e. Worker queue: arq+Redis vs Postgres-backed; recommend one.
Acceptance (end-to-end check at phase end):
- docker compose up works on amd64 and arm64 (CI builds both images); health endpoints green via `make e2e`.
- Spike results recorded with versions in docs/versions.md.
- git status shows no secrets, .env files, data or build output.
Finish with docs/phase-reports/P0.md.
```

**You check:** run `docker compose up` locally; read the spike results; approve the SysML and queue recommendations (ADR if they change a decision).

### P1 — Domain model, library and Karnataka pack

```text
Phase 1 — Domain model, reference library, Karnataka data pack, golden fixtures.
Read first: CLAUDE.md, docs/03, docs/04, docs/05 §1–3, docs/10, docs/12 ADR-002, 009, 010, 016.
Plan first, then build:
1. packages/domain: Prov[T], all stage artifact models (ProductSpec … ReportPack), validators of docs/04 §5, JSON Schema export, TS generation into packages/ts-types.
2. library/catalog: YAML schema and seed covering both golden products (docs/10 §1.3) with ranges and sources/estimate labels.
3. library/packs/KA: values of docs/10 §2.1 with effective dates, sources, verify flags. Prefer primary sources (gazette, KERC order, BIS/BEE). Mark anything unconfirmed verify: true.
4. Loader into Postgres with versions; pgvector index for catalog search.
5. golden/: G1–G7 inputs and expected outputs (docs/14 §4) as fixtures.
Acceptance (end-to-end check at phase end):
- The loader's catalog and pack validation (docs/10 §1.4, §2.3) passes when loading into Postgres.
- Golden routings for G1 and G2 can be expressed using catalog codes only.
- Schema round-trip: Pydantic → JSON Schema → TS types compile.
Finish with docs/phase-reports/P1.md listing every pack value with its source and verify status.
```

**You check:** open the pack table in the report; spot-check three values against their sources.

### P2 — Harness, wizard and intake

```text
Phase 2 — Pipeline harness, web wizard, S1 intake.
Read first: CLAUDE.md, docs/02 (all), docs/03 §1–3, docs/11 §2, docs/12 ADR-001, 003, 004, 005, 012, 015.
Plan first, then build:
1. packages/agents: LangGraph parent graph with all 13 stage nodes (stubs except S1), stage subgraph template (load → plan → tools → draft → validate/repair → compute → summarise → interrupt), Postgres checkpointer.
2. StageRun lifecycle, inputs_hash, stale propagation, change-set application (docs/02 §2, §6).
3. LLM router with roles, budgets, 429 backoff, fallbacks, llm_call logging; record/replay of LLM responses (cassettes) for the end-to-end check.
4. apps/web: JSON-Schema-driven wizard (5 steps + review, samples for G1/G2), pipeline view (React Flow), generic stage screen (artifact, provenance badges, questions, approve/edit/re-run/reject), SSE progress. Clean, modern UI in light and dark themes, accessible menus, purposeful animation for step changes and progress (CLAUDE.md owner's rules 4–5).
5. S1 intake: processors for wizard text, PDF, STEP (cad-worker sandbox), STL, photos (vision role); merge rules and questions (docs/03 §3).
Acceptance (end-to-end check at phase end):
- G1 and G2 wizard samples run S1 to awaiting_approval with cassettes; the STEP sample yields an assembly tree and GLB.
- Editing an approved S1 marks downstream stubs stale.
- Budget exhaustion pauses a stage with a resumable state.
Finish with docs/phase-reports/P2.md.
```

**You check:** create a project from the fan sample, upload a STEP and a photo, answer a question, approve.

### P3 — BoM, routing, quality, vendors

```text
Phase 3 — S2 BoM, S3 routing, S4 quality plan, vendor research.
Read first: CLAUDE.md, docs/03 §4, docs/05 §1–2, docs/09 §2, docs/10 §1, docs/12 ADR-013, 017, 018.
Plan first, then build:
1. S2: user-BoM normaliser; archetype skeleton + reasoner drafting; EBOM→MBOM engine; make/buy decisions with catalog-based preview (capex/area/headcount).
2. S3: process selection from catalog; time standards in priority order with formula evaluator (safe expression engine, no eval); precedence graph; validators.
3. S4: quality layers, AQL table lookup (ISO 2859-1 level II), inspector hours, inline checks injected as routing ops, FPY-based rework sizing, compliance links from the pack, power bank test-policy toggle.
4. Vendor research agent (parallel after S2): search, fetch (SSRF guard), evidence-required extraction, scoring, dedupe, shared cache with TTL.
Acceptance (end-to-end check at phase end):
- G1/G2 produce complete MBOMs and routings; all validators pass; LLM-estimated times are flagged.
- AQL sample sizes in the G1/G2 quality plans match the standard table.
- In the G1 vendor run (recorded pages), every vendor shown has a source URL; a planted page without evidence produces no vendor.
Finish with docs/phase-reports/P3.md.
```

### P4 — Capacity and inventory

```text
Phase 4 — S5 capacity and line balancing, S6 inventory.
Read first: CLAUDE.md, docs/05 §3, docs/06 §1, docs/12 ADR-006, 013, 014.
Plan first, then build:
1. Operating pattern input; takt and PCT; sizing rules for every task type (docs/05 §3.2).
2. CP-SAT balancing (mixed-model weights, per-variant overload, zoning, machine pins), RPW fallback, KPIs.
3. Strategies A/B/C with comparison table and recommendation; custom workcenters UI.
4. S6: ABC, lead times (vendor or catalog), safety stock, ROP, storage sizing by class, MHE, flow matrix.
Acceptance (end-to-end check at phase end):
- G1/G2 exact numbers (docs/14 §4) and G5 benchmark optima.
- Strategy comparison renders for G1 and G2; choosing one flows downstream.
- Every station assignment in G1/G2 and the benchmarks respects precedence and load limits.
Finish with docs/phase-reports/P4.md with the strategy tables for G1 and G2.
```

### P5 — Work orders, shifts, utilities

```text
Phase 5 — S7 work orders and shifts, S8 utilities.
Read first: CLAUDE.md, docs/06 §2–3, docs/10 §2.
Plan first, then build:
1. WO template, types, traceability, release sequence per strategy, sample week, draft work instructions (writer role, marked draft).
2. Roster: direct, relievers, supervisors, indirect ratios, compliance checks against the KA pack, welfare zones, labour cost.
3. Utilities: electrical (connected → MD → CD → supply → transformer/DG/UPS/APFC, kWh, tariff cost), air, HVAC (incl. test-rack heat), water, fire; utility rooms as zones.
Acceptance (end-to-end check at phase end):
- G3 transformer = 250 kVA; compliance check rejects a roster exceeding weekly hours.
- In G1/G2, every equipment item's load is counted exactly once in the utility totals.
Finish with docs/phase-reports/P5.md.
```

### P6 — Layout and 3D

```text
Phase 6 — S9 layout, 2D/DXF, 3D viewer and editing.
Read first: CLAUDE.md, docs/07 (all), docs/12 ADR-006, 008.
Plan first, then build:
1. Relationship chart; CP-SAT block layout with candidate shapes, NoOverlap2D, pins, keep-apart, aisles; CRAFT fallback.
2. Line layout (I/U/L); greenfield building + site plan; existing shed mode with columns and shortfall report.
3. Layout checks in Python (authoritative) and TypeScript (live feedback).
4. 2D SVG, DXF export, scene.json; R3F viewer with instancing, overlays, side panel; drag/rotate/resize with live checks; re-optimise around pins. Smooth camera and selection animation; viewer, panels and overlays look right in light and dark themes.
Acceptance (end-to-end check at phase end):
- G6 equals brute force; G1/G2 layouts have zero violations in both modes.
- ≥ 30 fps for G1/G2 scenes on a mid-range laptop; browser and server checks agree on the G1/G2 layouts.
Finish with docs/phase-reports/P6.md with screenshots.
```

### P7 — Simulation

```text
Phase 7 — S10 simulation.
Read first: CLAUDE.md, docs/08 (all), docs/02 §6.
Plan first, then build:
1. Model generator from artifacts; distributions, breakdowns, yield/rework, buffers, operators, handlers, batches, calendars, changeovers.
2. Experiments with seeds, CIs, scenarios with common random numbers, time budget.
3. KPIs and charts; bottleneck (active-period method); 3D heatmap overlay.
4. Recommendations as change sets; auto-run proposed scenarios; apply flow.
Acceptance (end-to-end check at phase end):
- G7 cases; G1/G2 simulated throughput never exceeds the analytic bound; same seeds give identical results.
- G1 baseline and one applied improvement shown end to end.
Finish with docs/phase-reports/P7.md.
```

### P8 — Finance, reports, SysML, admin

```text
Phase 8 — S11 finance, S12 reports, SysML export, admin page.
Read first: CLAUDE.md, docs/09, docs/04 §6, docs/10 §3.
Plan first, then build:
1. Finance engine (capex groups, working capital, opex, P&L, depreciation, financing, NPV/IRR, break-even, sensitivity) with low/base/high.
2. Report pack: web, PDF, XLSX, DXF, SysML (.sysml validated by sysmlpy), ZIP export; assumptions register; disclaimers.
3. Admin page: browse/edit/validate/publish catalog and pack versions, diff, test bench; Cloudflare Access + passcode.
Acceptance (end-to-end check at phase end):
- G4 exact; totals reconcile with source artifacts.
- Generated SysML for G1/G2 parses without errors.
- A catalog edit published in admin changes a new project's result but not an existing pinned project.
Finish with docs/phase-reports/P8.md.
```

### P9 — Production hardening and deploy

```text
Phase 9 — Deploy to Oracle Always Free and harden.
Read first: CLAUDE.md, docs/11 (all), docs/12 ADR-011, 012.
Plan first, then build:
1. Provision A1 at exactly 2 OCPU / 12 GB; Docker Compose prod; Caddy; Cloudflare DNS, Pages, Turnstile, Access.
2. Rate limits, global LLM budget, queue with position, BYO key, upload limits and sandbox, retention job, backups to Object Storage, restore runbook.
3. Monitoring, alerts, usage dashboard.
Acceptance (end-to-end check at phase end):
- Two concurrent G1/G2 runs complete on the VM within budget; a third queues.
- Retention job deletes an expired test project and its files; restore from backup tested.
- Security checklist (docs/11 §2–3) verified item by item in the report.
Finish with docs/phase-reports/P9.md.
```

## 6. Definition of done (every phase)

- The phase's end-to-end check passes (golden cases through the real pipeline, recorded LLM responses); lint and type checks clean; TS types regenerate at build.
- Nothing committed or pushed by Claude Code; changed files listed in the phase report.
- `.gitignore` covers every new secret, generated or build file; no secret in source or content.
- UI phases: menus keyboard-accessible, both themes checked, animation respects reduced motion.
- Engines contain no LLM calls; agents never write engine numbers.
- New dependencies have permissive licences and are recorded in `docs/versions.md`.
- Golden fixtures unchanged, or changes explained.
- Phase report written; docs updated where behaviour changed (ADR if a decision changed).

## 7. Your review checklist

| Check | How |
|---|---|
| It runs | Run the phase feature yourself with the fan and power bank samples |
| Numbers are engine numbers | Pick one number on screen; find the engine function that produces it |
| Provenance shows | Low-confidence values are visibly marked and appear in the assumptions list |
| The check is real | Open the end-to-end check and read what it asserts for G1/G2 |
| Safe to commit | Run `git status` and `git diff`: no `.env`, keys, certificates, `data/`, `node_modules/` or build output before you commit and push |
| Looks right | Open the main screens in light and dark theme; use the menus with the keyboard only |
| Quota-safe | `llm_call` totals for a full golden run are within the budget in docs/02 §4 |
| Data not code | No tariff, wage, time or price constants hard-coded in engines |
| Docs current | Phase report and any ADR updates present |
