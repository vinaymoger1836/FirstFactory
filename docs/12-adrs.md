# 12 — Architecture decision records

Status values: **Accepted**, Proposed, Superseded. New or changed decisions during implementation are added here as *Proposed* and approved by the owner.

---

### ADR-001 — LLM proposes, engines calculate, user approves
**Status:** Accepted
**Context:** Users will spend real money on the outputs; LLM arithmetic and recall are unreliable and free-tier tokens are scarce.
**Decision:** Agents choose, draft, extract and explain. All numbers come from deterministic, tested Python engines. Every stage ends in a human approval.
**Alternatives:** End-to-end LLM generation (fast to build, unverifiable); fully rule-based wizard (rigid, no understanding of new products).
**Consequences:** More engine code and tests; much lower token use; reproducible results; clear provenance.

### ADR-002 — Typed Pydantic domain model is the model of record; SysML v2 is an export
**Status:** Accepted
**Context:** SysML v2 is the required standard, but LLMs write invalid SysML v2 often and open-source tooling is young (several libraries pre-alpha).
**Decision:** Store and exchange Pydantic models (JSON). Generate SysML v2 textual notation from approved artifacts with our own serializer; validate with `sysmlpy` in CI; evaluate `sysml2kit` in P0.
**Alternatives:** SysML v2 as the master model with agents editing it; SysML v2 API server as the store.
**Consequences:** Simple, typed agent I/O; SysML stays standards-faithful for the supported subset; the mapping (doc 04 §6) must be maintained.

### ADR-003 — LangGraph for orchestration; domain tables are the system of record
**Status:** Accepted
**Decision:** LangGraph graph per project, Postgres checkpointer, `interrupt` for approvals. Graph state holds only IDs; artifacts live in `stage_run`.
**Alternatives:** Own lightweight harness; Temporal/Prefect.
**Consequences:** Pause/resume and human-in-the-loop out of the box; graph can evolve without migrating stored projects.

### ADR-004 — LiteLLM router with roles: Groq for text, Gemini for vision
**Status:** Accepted
**Decision:** Agents call roles (`reasoner`, `extractor`, `vision`, `writer`); config maps roles to models with budgets and fallbacks.
**Consequences:** Can switch to paid models per role later; vision does not depend on Groq; quotas enforced centrally.

### ADR-005 — Versioned stage runs in a DAG with stale propagation
**Status:** Accepted
**Decision:** Each stage writes immutable versions; an inputs hash detects staleness; edits upstream mark downstream stale; change sets carry feedback from simulation/layout.
**Consequences:** Interactive iteration without losing work; exports are reproducible.

### ADR-006 — OR-Tools CP-SAT for line balancing and block layout, with heuristic fallbacks
**Status:** Accepted
**Decision:** CP-SAT models with time limits (30 s balancing, 60 s layout); RPW heuristic and CRAFT-style exchange as fallbacks; results labelled optimal/feasible/fallback.
**Alternatives:** Pure heuristics; MILP with CBC; metaheuristics (GA/SA).
**Consequences:** Strong solutions on 2 cores for POC sizes; need benchmark tests.

### ADR-007 — SimPy discrete-event simulation generated from artifacts
**Status:** Accepted
**Decision:** Model generated automatically; 20 days × 10 seeded replications default; common random numbers for scenarios; event log for later replay.
**Alternatives:** Commercial DES tools; analytic queueing only.
**Consequences:** Free and scriptable; performance must be watched (aggregated events).

### ADR-008 — 3D via React Three Fiber from a 2D block layout
**Status:** Accepted
**Decision:** Solve layout in 2D; generate a scene graph; render with R3F using extruded footprints and instancing; optional GLB per machine class.
**Alternatives:** Babylon.js; Unity WebGL; true 3D optimisation.
**Consequences:** Fast, editable, light; visuals are schematic, not photoreal.

### ADR-009 — Versioned process catalog (YAML + admin page); projects pin versions
**Status:** Accepted
**Decision:** Catalog as YAML seed, loaded to Postgres, edited through an admin page producing new versions; projects pin a version; upgrades are explicit and mark stages stale.
**Consequences:** Knowledge improves without code releases; results stay reproducible.

### ADR-010 — Regional data packs with effective dates and sources (Karnataka first)
**Status:** Accepted
**Context:** Tariffs, wages and labour rules change mid-cycle (e.g. KERC revised a multi-year order in 2026; labour code rules notified in 2026).
**Decision:** Every regional value has effective dates, source URL, retrieval date, confidence and a `verify` flag.
**Consequences:** Reports can cite and date every regulatory value; maintenance cadence needed (quarterly check).

### ADR-011 — Hosting on Oracle Always Free Ampere A1 + Cloudflare Pages
**Status:** Accepted
**Context:** Must be free; heavy Python deps (OCP, OR-Tools); HF Spaces Docker now paid; Render free too small.
**Decision:** One ARM VM at exactly 2 OCPU / 12 GB with Docker Compose; frontend on Cloudflare Pages.
**Consequences:** ARM builds required; single point of failure accepted for POC; strict concurrency limits.

### ADR-012 — No login: capability links, Turnstile, rate limits, 30-day retention
**Status:** Accepted
**Decision:** Unguessable project links; Turnstile; per-IP and global limits; global LLM budget; optional BYO Groq key; projects deleted 30 days after last activity with ZIP export.
**Consequences:** Zero sign-up friction; anyone with a link has access (stated clearly); accounts can be added later without changing the data model (project ownership column reserved).

### ADR-013 — Quality plan before capacity; operating pattern is a capacity input
**Status:** Accepted
**Decision:** S4 quality runs before S5 so 100% inline checks are balanced as tasks; shifts/days/OEE are captured at S5 start; S7 builds rosters on that pattern.
**Consequences:** Correct takt and station counts; changing the pattern re-runs capacity.

### ADR-014 — Two variants; compute mixed-model, dedicated and batch strategies side by side
**Status:** Accepted
**Decision:** S5 always produces strategies A/B/C; user chooses; only the chosen one flows downstream; others remain in the report.
**Consequences:** Shows how the line changes with variants; more engine work in S5.

### ADR-015 — Web wizard is the only template entry in the POC
**Status:** Accepted
**Decision:** JSON-Schema-driven wizard with samples; Excel template deferred.
**Consequences:** One input path to test; Excel import later maps to the same schema.

### ADR-016 — Provenance and confidence on every value
**Status:** Accepted
**Decision:** `Prov[T]` wrapper; UI badges; assumptions register generated automatically.
**Consequences:** Larger payloads; trustworthy reports; easy review of weak spots.

### ADR-017 — Make/buy for golden products
**Status:** Accepted
**Decision:** Power bank: buy PCBA and cells; in-house grading, welding, assembly, test. BLDC fan: buy controller PCBA, rotor/shell, blades; in-house stator winding, varnish, motor and final assembly, test.
**Consequences:** Realistic first factories; SMT remains in the catalog for other products.

### ADR-018 — Power bank test policy: 100% functional + sampled capacity test (toggle to 100% capacity)
**Status:** Accepted
**Consequences:** 8 vs 398 capacity channels in the golden case — shown to users as a design lever.

---

ADR-019 to ADR-021 were raised during P0 and P1 and exist in the repo's `docs/12-adrs.md`; they are summarised here so this package stays in step. They stay *Proposed* until the owner approves them.

### ADR-019 — Worker queue: procrastinate (Postgres), not arq + Redis
**Status:** Proposed (P0)
**Decision:** Background jobs run on procrastinate over Postgres, one queue per worker (`engines`, `cad`), with a one-minute stalled-job sweep. Redis stays for worker heartbeats only.
**Why:** In the P0 bench, enqueue-to-start p50 was 6 ms against 48 s for arq at its default poll; recovery after a killed worker is heartbeat-based (30 s) instead of growing with `job_timeout`; arq is in maintenance mode; Postgres is already the system of record.

### ADR-020 — Library search embeddings: deterministic hashed n-gram vectors
**Status:** Proposed (P1)
**Decision:** `catalog.search` uses 256-dimension hashed n-gram vectors plus full-text rank, stored in pgvector.
**Why:** No embedding model was specified, and the loader must work with no network or quota. A learned embedding can replace it later behind the same search function.

### ADR-021 — Library loader in its own package (`packages/library`)
**Status:** Proposed (P1)
**Decision:** YAML loading, catalog and pack tests, versioned Postgres load and the `fl-library` CLI live in `packages/library`, not in `packages/domain` or `packages/engines`.
**Why:** Keeps the domain model and engines free of database and file-system code.

### ADR-022 — BoM is the centre of the pipeline; three intake cases; suggestions, not silent additions
**Status:** Accepted (owner, 2026-11-13)
**Context:** CAD files do not contain cells, ICs, labels, packaging or consumables, so a BoM cannot be derived from CAD alone; users may bring a full BoM, a partial one or nothing. Every later stage reads the MBOM.
**Decision:** S2 handles three cases (CAD only, complete BoM, incomplete BoM; doc 03 §4.1). User BoM lines are never rewritten. Probable gaps (found against the archetype skeleton by commodity class, and against CAD) are shown as **suggestions** the user accepts or rejects. Each line records `origin` and `review`. The wizard asks "Do you have a BoM?" as a hint only. EBOM and MBOM are both delivered as a BoM workbook.
**Alternatives:** Require a BoM (blocks users without one); derive the BoM from CAD alone (incomplete); add missing parts automatically (hides AI guesses inside the user's BoM).
**Consequences:** More S2 UI (editable review table); additive model changes in P3 (`BomLine.origin`, `BomLine.review`, `BomFinding`); a clear approval gate on a reviewed BoM.

### ADR-023 — Factory Package: documents for building and running the factory
**Status:** Accepted (owner, 2026-11-13)
**Context:** The platform's output should cover everything needed to build and run a factory, including work instructions and quality documents, not only reports.
**Decision:** Add a Tier 1 document set (work instruction per station, inspection instruction and check sheet per check, process flow chart, control plan, SOP set, training matrix, equipment list; doc 15). Documents are generated in a documents step inside S12 from engine-built skeletons; the `writer` role writes wording only; coverage validators check that no operation, BoM line, check or hazard is dropped. All documents are labelled *Draft — validate on the shop floor*. Work-instruction drafting moves out of S7. Export as PDF, XLSX and editable DOCX (`python-docx`, to be checked for licence and arm64 at P8b). Built in phase P8b.
**Alternatives:** Generate work instructions in S7 (earlier, but stale after layout or simulation changes and token cost paid twice); free-text LLM documents (cannot guarantee safety or quality content is present); defer all documents (the deliverable would stop short of what a first factory needs).
**Consequences:** About 60k extra tokens per product (estimate, measured in P8b) with per-document caching; more review work for the user; Tier 2 documents (PFMEA, maintenance plan, calibration register, EHS plan, commissioning checklist) are not in the POC.
