# 08 — Simulation

## 1. Purpose

Prove (or disprove) that the designed factory meets the target volume under variability, and find the bottleneck. Static capacity maths (S5) assumes averages; the simulation adds variability, breakdowns, rework, buffers, sequencing, shared operators, material handling and shift calendars.

## 2. Model generation

A SimPy model is generated from approved artifacts — no hand modelling.

| Element | Source | SimPy representation |
|---|---|---|
| Units (with variant) | Release plan (S7) | Entities created per the WO sequence |
| Stations / test positions | S5 chosen strategy | `Resource(capacity = parallel positions)` |
| Operators | S7 roster | `Resource` pools; stations request operator + station when shared |
| Buffers | S5 (default 2 per station, editable) | `Store` with capacity → blocking when full |
| Batch processes (oven) | S5 | Process waits for batch size or max wait time |
| Off-line test racks | S5 | `Resource(capacity = channels)`; units sampled per test policy |
| Processing times | S3 time standards | Distribution per op and variant (triangular default) |
| Breakdowns | Catalog MTBF/MTTR per machine class | Interrupts with exponential TBF/TTR |
| Yield / rework | Catalog FPY per process | Probability of fail → rework station → re-test |
| Changeovers | S5 strategy C | Setup delay when variant changes |
| Material handling | S6 MHE + S9 distances | Handler resource; travel time = distance / speed (+ pick time) |
| Calendar | S7 roster | Breaks and shift ends pause stations (or not, per policy) |

## 3. Experiments

- **Baseline experiment (default):** warm-up 1 day, run 20 working days, **10 replications** with fixed seeds (`seed_base + i`).
- Outputs reported as mean with 95% confidence interval across replications.
- Runs in the worker process; per-run time budget (default 120 s total); the model uses aggregated events (no per-second ticks) to stay within budget on 2 cores.
- **Scenarios:** user or agent-defined variants of the baseline (buffer sizes, extra station, mix change, sequence rule, second shift, test policy toggle). Each scenario is a parameter diff, run with the same seeds (common random numbers) for a fair comparison.

## 4. KPIs

| KPI | Detail |
|---|---|
| Throughput | Units/day per variant vs target; % of target with CI |
| Station states | % busy / blocked / starved / down / setup / off-shift |
| Bottleneck | Active-period method: station with the longest uninterrupted active periods most of the time; shifting bottlenecks reported |
| WIP and lead time | Average and P95 per variant |
| Buffer occupancy | Histogram per buffer |
| Operator utilisation | Per pool |
| OEE estimate | Per station and line |
| Rework load | Units/day and rework station utilisation |

UI: KPI cards, utilisation stacked bars per station, WIP over time, buffer histograms, bottleneck highlight on the layout (3D heatmap overlay).

## 5. Recommendations loop

1. The `reasoner` reads KPIs and the bottleneck analysis and proposes up to 5 fixes with expected effect, each expressed as a **change set** (doc 02 §6): add parallel position, increase buffer, rebalance tasks, re-sequence, move zones closer, add handler, second shift.
2. The engine runs each proposed fix as a scenario automatically (budget permitting) and shows measured effect.
3. The user applies one: the target artifact (capacity, workorders_shifts or layout) gets a new version; downstream stages go stale; "re-run from here".

## 6. Validation rules (built in)

- Simulated throughput ≤ analytic capacity bound from S5 (`min over stations of positions × available time / cycle`), within statistical noise. Violation = model bug → test failure.
- Deterministic serial line (no variability) reproduces `1 / max(cycle)` throughput exactly.
- Single-server queue with exponential times at ρ = 0.8 reproduces analytic mean queue length (L = 4) within the CI.
- Same seeds → identical results (determinism test).

## 7. Event log

Each run writes a compact event log (Parquet: time, entity, variant, station, event) used for charts now and for a **3D replay** later (units moving through the layout).
