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

### ADR-019 — Postgres-backed job queue (procrastinate) instead of arq + Redis
**Status:** Proposed (P0, spike e; awaiting owner approval)
**Context:** docs/02 §3 left the worker queue open ("arq + Redis or a Postgres-backed queue; P0 decides"). Jobs are few and long (CAD extraction, CP-SAT up to 60 s, simulation for minutes). docs/02 §7 requires a crashed worker's job to be retried. The VM is 2 OCPU / 12 GB, and Postgres is already the system of record (ADR-003).
**Decision:** Use procrastinate 3.x on the existing Postgres. Each worker runs at concurrency 1 on its own queue (`engines`, `cad`). A periodic sweep retries jobs whose worker missed heartbeats (stalled after 30 s). Redis stays for rate limits, token buckets and heartbeats only; it holds no durable job state.
**Alternatives:** arq + Redis. arq is in maintenance-only mode (its README), pins `redis<6`, and recovers a killed job only after `job_timeout + 10 s`, so a long job's timeout would delay recovery by the same amount. Hand-rolled `SKIP LOCKED` was rejected as more code to own.
**Evidence (P0, amd64, one worker at concurrency 1):** enqueue→start p50 6.4 ms / p95 25 ms (arq: seconds, poll-bound); 200-job drain 1.3 s (arq 10–100 s depending on `poll_delay`); SIGKILL mid-job, then recovered in 30.5 s (arq 38 s with a 30 s `job_timeout`); idle worker RSS about 48 MB for both.
**Consequences:** One fewer durable store to back up. Jobs can later be enqueued in the same transaction as the `stage_run` row. The cad-worker needs Postgres access on its internal network: give it a dedicated DB role limited to the queue tables (P9). Procrastinate is also looking for maintainers; its scope is small enough to replace if needed.
