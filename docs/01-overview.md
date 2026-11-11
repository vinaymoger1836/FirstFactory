# 01 — Overview

## 1. Problem

An entrepreneur with a product design (a power bank, a ceiling fan, a small appliance) who wants to manufacture it must answer dozens of linked questions before spending money: what to make and what to buy, which machines, how many stations and operators, which quality checks, how much stock and floor space, how many shifts, how much power, what the building looks like, what it all costs, and who supplies it. Today this means several consultants and months of iteration.

The platform answers these questions in one guided, interactive flow. The user fills in a product template; a sequence of agent-driven stages proposes each part of the factory; deterministic engines compute every number; the user reviews and approves each stage; the result is an interactive 3D factory, a simulation that proves it can hit the target volume, and a report pack.

## 2. Users

| User | Needs |
|---|---|
| Entrepreneur (primary) | Plain-language guidance, sensible defaults, clear costs, a layout they can show investors and contractors |
| Their advisor / engineer | Visible assumptions, provenance of every number, ability to override anything, exports (DXF, XLSX, SysML) |
| Platform admin (us) | Edit the process catalog and regional data packs without a code release |

No logins in the POC. A project is reached through its private link (doc 11).

## 3. Scope

**In scope (POC)**

- Discrete assembly: simple electronics and mechanical products.
- Two golden products: power bank and BLDC ceiling fan, two variants each.
- India; Karnataka is the first regional data pack.
- Greenfield sites (we size the building) and existing sheds (fixed boundary and columns).
- Full output set: BoM, routing, quality plan, three line strategies, inventory, work orders, shifts, utilities, layout 2D/3D, simulation, finance, vendors, compliance checklist, SysML v2 export.

**Out of scope (POC)**

- Process manufacturing (food, chemicals, pharma), heavy fabrication, cleanrooms beyond ESD zones.
- In-house PCB assembly for the golden products (PCBA is bought in; the catalog still contains SMT for other products).
- MES / ERP execution. The platform designs the work-order system; it does not run production.
- Certified engineering drawings. Outputs are planning-grade estimates (see §6).
- Mobile apps, user accounts, multi-user collaboration.

## 4. Principles

1. **LLM proposes, engine calculates, user approves.** Agents read inputs, choose from the catalog, draft and research. Every number (takt, station counts, kVA, m², ₹) comes from deterministic, tested Python. (ADR-001)
2. **Every value carries provenance.** `source` ∈ {user, cad, datasheet, vision, llm, library, web, engine} plus a confidence 0–1 and a note. Low-confidence values are visibly marked and collected in the report's assumptions register.
3. **A dependency graph, not a wizard.** Stages are versioned. Editing an upstream stage marks downstream stages stale; nothing upstream is lost. (ADR-005)
4. **Data, not code.** Processes, machine classes, tariffs, wages, labour rules and certifications live in versioned data (catalog and data packs) with effective dates and sources. (ADR-009, ADR-010)
5. **Never invent the world.** No vendor, regulation or price appears without a source URL or a library entry. Unknowns are shown as unknowns.
6. **Free and small.** Everything runs on free tiers and one 2-core / 12 GB server; the design budgets tokens, CPU and memory accordingly.

## 5. System context

```
Entrepreneur ──browser──▶ Web app (React, Cloudflare Pages)
                              │  HTTPS + SSE
                              ▼
                        API (FastAPI) ── Orchestrator (LangGraph) ── Agents
                              │                    │                    │
                              │                    ▼                    ▼
                              │              Engines (pure Python)   LLM router (LiteLLM)
                              │              balancing · layout ·        ├─ Groq (text)
                              │              sim · utilities ·           └─ Gemini (vision)
                              │              inventory · finance
                              ▼                                       Web search (Tavily / SearXNG)
                        PostgreSQL (+pgvector) · file store (CAD, photos, exports)
                              ▲
Admin ──Cloudflare Access──▶ Admin page (catalog + data packs)
```

## 6. Outputs and their status

| Output | Format | Grade |
|---|---|---|
| Product spec, BoM (EBOM → MBOM) | Web, XLSX | Planning |
| Routing, time standards, precedence graph | Web, XLSX | Planning; LLM-estimated times flagged |
| Quality plan (IQC / IPQC / EOL / OQC / lab) | Web, PDF | Planning; certification list is informational |
| Line design — three strategies | Web, XLSX | Planning |
| Inventory policy, storage zones | Web, XLSX | Planning |
| Work-order templates, sample week, draft work instructions | Web, PDF | Draft |
| Rosters, headcount, compliance check | Web, XLSX | Planning; not legal advice |
| Utilities (kVA, transformer, DG, air, HVAC, water, fire) | Web, PDF | Budgetary; needs a licensed electrical/MEP engineer |
| Layout 2D / 3D, site plan | Web, SVG, DXF | Concept; needs an architect for approvals |
| Simulation results | Web, CSV | Planning |
| Financials | Web, XLSX, PDF | Budgetary ranges (low / base / high) |
| Vendor report | Web, XLSX | Leads with sources, not endorsements |
| SysML v2 model | `.sysml` | Model of the designed system |

Every report carries a disclaimer: planning estimates, not engineering certification, legal or financial advice.

## 7. Tech stack (all free)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | React + Vite + TypeScript | Hosted on Cloudflare Pages |
| 3D | React Three Fiber + drei | Instanced meshes for racks and test channels |
| Pipeline UI | React Flow | Stage graph with status and staleness |
| Charts | ECharts | Utilisation, Gantt, tornado, histograms |
| Forms | JSON-Schema-driven wizard | Schema generated from Pydantic |
| API | FastAPI (Python 3.12) | SSE for progress |
| Orchestration | LangGraph | Postgres checkpointer; interrupts for approval |
| LLM access | LiteLLM | Groq (text), Gemini (vision); budgets per stage |
| Domain model | Pydantic v2 | JSON Schema → TypeScript types |
| Solvers | OR-Tools (CP-SAT) | Line balancing, layout |
| Simulation | SimPy | Worker process, seeded replications |
| CAD | CadQuery / OCP, trimesh | STEP and STL; GLB for the viewer |
| Finance | numpy-financial | NPV, IRR, schedules |
| Research | Tavily free tier or self-hosted SearXNG; trafilatura | Public pages only |
| SysML v2 | Own serializer on the domain model; validated with `sysmlpy` | `sysml2kit` evaluated in P0 |
| Database | PostgreSQL 16 + pgvector | On the same VM |
| Reports | Jinja2 → Markdown/HTML → WeasyPrint PDF; openpyxl; ezdxf | |
| Hosting | Oracle Always Free Ampere A1, Docker Compose, Caddy | Cloudflare Pages, Turnstile, Access |

## 8. Glossary

| Term | Meaning |
|---|---|
| Takt | Net available time ÷ demand; the pace the line must meet |
| Planned cycle time (PCT) | Takt × target OEE; what stations are balanced to |
| OEE | Overall equipment effectiveness (availability × performance × quality) |
| SALBP-1 | Simple assembly line balancing: minimise stations for a given cycle time |
| EBOM / MBOM | Engineering BoM (what it is) / manufacturing BoM (how it is built) |
| IQC / IPQC / EOL / OQC | Incoming / in-process / end-of-line / outgoing quality control |
| FPY | First-pass yield |
| AQL | Acceptable quality limit (sampling plans, ISO 2859-1 / IS 2500) |
| SLP | Systematic layout planning (closeness ratings A/E/I/O/U/X) |
| MD / CD | Maximum demand / contract demand (kVA) |
| Capability link | Unguessable project URL used instead of a login |
