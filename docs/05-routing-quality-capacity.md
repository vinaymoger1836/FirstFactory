# 05 — Routing, quality plan and capacity

## 1. Stage S3 — Process plan and routing

### 1.1 Building operations

For each `make` item and each assembly level of the MBOM, the agent selects processes from the catalog (`catalog.search`) and builds operations `OP-010, OP-020, …` with: process code, workcenter class, consumed lines, produced item, optional quality gate, batch spec, and time standard. Operations that differ by variant carry `variant_times`.

### 1.2 Time standards (priority order)

1. **User value** (`method=user`).
2. **Catalog formula** (`catalog_formula`), evaluated by the engine with product parameters. Examples:
   - screwing: `n_screws × 4.0 s + 3 s handling`
   - spot welding: `n_welds × 1.5 s + 8 s load/unload`
   - functional test (power bank): fixed `60 s` (catalog default, editable)
   - capacity test: `(capacity_mAh / charge_current_mA + capacity_mAh / discharge_current_mA) × 3600 s + rest`
   - stator winding: `turns × slots / rpm × 60 + 25 s`
   - Hi-pot: `15 s`; fan run-in test: `300 s`
3. **MTM-style table** for manual elements (`mtm_table`): reach, grasp, place, insert, label, pack — per element times from a library table.
4. **LLM estimate** (`llm_estimate`), confidence < 0.5, flagged red in the UI and listed in the assumptions register.

Each time standard carries a distribution for simulation (default triangular ±15%).

### 1.3 Precedence graph

Built from the operation sequence plus BoM structure (sub-assemblies must complete before their consumer). The agent may declare parallelisable operations; the engine checks acyclicity and that every consumed line is produced earlier or bought.

### 1.4 Golden routings (abbreviated)

Power bank: IQC cells (grading: OCV + IR) → cell sorting/matching → pack spot welding (nickel strip) → pack insulation → pack-to-PCBA solder → pack voltage/IR inline test → enclosure assembly + screws → functional test (100%) → [sampled capacity test] → labelling + QR → packing.

BLDC fan: stator winding → winding inspection (resistance, Hi-pot) → varnish dip + oven cure (batch) → motor assembly (stator + shaft + bearings + rotor shell) → controller PCBA fit + harness → motor test → final assembly (canopy, down-rod kit) → Hi-pot (100%) → run-in test (100%) → labelling (incl. BEE label) → packing (blades packed separately).

## 2. Stage S4 — Quality plan

Runs **before** capacity so that 100% inline checks are balanced as line tasks (ADR-013).

| Layer | Content | Sizing |
|---|---|---|
| IQC | Incoming inspection per bought item class (cells, PCBAs, molded parts, rotors, blades) | Sample size from ISO 2859-1 / IS 2500 table by lot size, inspection level (II) and AQL; inspector hours = Σ samples × check time; quarantine area from lots on hold |
| IPQC | First-article inspection per shift/changeover; patrol checks (frequency × duration); 100% inline tests (pack voltage/IR, winding resistance, Hi-pot) | Inline tests → routing operations; patrol → inspector hours |
| EOL / FQC | 100% functional test: power bank (output V/I per port, protection trip OCP/SCP/OVP, LEDs); fan (RPM per speed, power draw, remote, noise check) | Line tasks, sized by test time vs PCT |
| OQC | AQL sampling of packed goods, drop test samples | Area + inspector hours |
| Lab / reliability | Power bank: capacity verification vs rated mAh, thermal, ageing samples. Fan: periodic air delivery / power (service value) checks, endurance samples | Equipment list + lab area |
| Rework & MRB | Rework stations and hold area | From catalog FPY per process: rework load = Σ (1 − FPY) × volume × rework time |

**Compliance list (Karnataka/India pack):** each archetype references required certifications and their implications, e.g. power banks under BIS CRS (finished product and cells registered separately; rated capacity verification for cells) and ceiling fans under the Electric Ceiling Type Fans QCO 2023 with mandatory BEE star labels. The quality plan links each certification to the checks that support it. These are informational and dated; the report states "verify with a certification body".

**Power bank test policy toggle:** default = 100% short functional test + sampled capacity test (2% of output); alternative = 100% capacity test. Both are computed so the user sees the impact (§3.5).

## 3. Stage S5 — Capacity and line balancing

### 3.1 Operating pattern and takt

Inputs captured at the start of S5 (defaults): 1 shift/day, 6 days/week, 300 working days/year, 8 h shift with 30 min of planned stoppages (two tea breaks + start/end routines) → **net available time 27,000 s/day**; lunch is outside working time within the permitted spread-over. Target OEE 0.85.

```
daily_demand        = annual_volume / working_days
takt_s              = net_available_s / daily_demand
planned_cycle_s     = takt_s × target_oee            # stations are balanced to this
```

| Golden case | Daily demand | Takt | PCT |
|---|---|---|---|
| Power bank, 3,00,000 / yr | 1,000 (600 PB10K + 400 PB20K) | 27.0 s | 22.95 s |
| Fan, 1,00,000 / yr | 333.3 (233.3 × 1200 mm + 100 × 900 mm) | 81.0 s | 68.85 s |

### 3.2 Task types and sizing rules

| Task type | Rule | Example (golden) |
|---|---|---|
| Manual line task | Assigned to stations by CP-SAT (§3.3) | Screwing, assembly, labelling |
| Machine-paced | `machines = ceil(cycle_s / PCT)` per station position | Spot welder, winding machine |
| Long test (parallel positions) | `positions = ceil(test_s / PCT)` | PB functional test 60 s → **3** positions; fan run-in 300 s → **5** positions |
| Off-line test racks (run unattended) | `channels = ceil(Σ_v units_v × test_h_v / rack_hours_per_day × (1 + margin))` | PB sampled capacity test (2%: 12 × 6 h + 8 × 10 h, 22 rack-h/day, 15% margin) → **8** channels; 100% policy → **398** channels |
| Batch process | `units = ceil(ceil(daily_qty / batch_size) × cycle_h / (available_h × availability))` | Fan varnish oven: 60 stators/batch, 2 h, 7.5 h × 0.85 → **2** ovens |
| Inspection (sampled) | `inspector_h = Σ samples × check_time` | IQC/OQC |

### 3.3 Line balancing engine (SALBP-1 / mixed-model)

CP-SAT model:

- Variables: `x[t, s] ∈ {0,1}` task t on station s (s ≤ upper bound from heuristic); `used[s]`.
- Constraints: each task exactly once; precedence: `station(t1) ≤ station(t2)`; station load `Σ_t w_t x[t,s] ≤ PCT` where `w_t` is the mix-weighted time; per-variant load `Σ_t w_{t,v} x[t,s] ≤ PCT × (1 + overload_allow)` (default 10%); zoning: tasks requiring the same fixture/ESD zone together, incompatible tasks apart; machine-bound tasks pinned to machine stations.
- Objective: minimise used stations, then minimise max station load (smoothness), lexicographically.
- Time limit 30 s; warm start and fallback = ranked positional weight heuristic.
- Outputs: stations, loads per variant, balance efficiency `Σ w / (stations × PCT)`, smoothness index, idle time.

### 3.4 Three strategies for two variants

| Strategy | How it is computed | Trade-offs shown |
|---|---|---|
| A. Mixed-model line | One balance with mix-weighted times and per-variant overload limit; sequence rule for release (S7) | Fewest stations; sensitive to sequence; checked in simulation |
| B. Dedicated lines | One SALBP-1 per variant with its own takt (variant demand) | More stations/fixtures; simplest operation |
| C. Batch with changeovers | One line balanced on the slower variant; batch size from changeover time and WIP limits (EPQ-style); setup losses reduce available time | Extra WIP and setup losses; fewer stations than B |

For each strategy the engine reports: stations, operators, machines, test positions, estimated line floor area (catalog footprints × layout factor), balance efficiency, utilisation per station per variant, capex delta (catalog prices). The agent writes a short recommendation; the user picks one. Only the chosen strategy flows downstream (others are kept for the report).

### 3.5 Outputs

`CapacityDesign { operating_pattern, takt, pct, strategies[A,B,C], chosen, workcenters[], stations[], equipment_list[], test_positions[], custom_workcenters[] }`.

Custom workcenters added by the user (name, footprint, operators, kW, utilities, optional ops) are included in all strategies and flow downstream like catalog ones.

### 3.6 Engine tests

- SALBP-1 benchmark instances from the published Scholl data set (e.g. Jackson, Mertens, Bowman) must reach the documented optimal station counts within the time limit.
- Golden fixture numbers in §3.1–3.2 are exact assertions.
- Property test: random DAGs — every solution respects precedence and load limits.
