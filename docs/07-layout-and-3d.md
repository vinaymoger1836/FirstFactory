# 07 — Layout and 3D

## 1. Inputs

| From | What |
|---|---|
| S5 capacity | Production workcenters, stations, machines with catalog footprints and heights; line strategy |
| S4 quality | IQC area, quarantine/MRB, lab, OQC area |
| S6 inventory | Storage zones by class, supermarkets, docks, flow matrix |
| S7 shifts | Welfare zones (canteen, creche, washrooms, first aid), offices, locker rooms |
| S8 utilities | Utility rooms and yards with placement constraints |
| ProductSpec.site | Greenfield plot or existing shed (walls, columns, doors, clear height) |
| Karnataka pack | Setbacks, ground coverage, parking norms, exit travel distance, aisle widths (sources dated; "verify with authority") |

## 2. Relationship chart

An SLP-style closeness rating between every pair of zones:

- From flows: ratings A/E/I/O by flow intensity quantiles (loads/day × unit-load weight).
- From rules (catalog + pack): e.g. Li-ion store **X** (keep apart) with canteen, offices and exits (min distance); IQC **A** with receiving dock; FG warehouse **A** with dispatch dock; lab **I** with EOL test; DG yard **X** with offices; compressor room **E** with production.
- The user can override ratings in the UI (`source=user`).

## 3. Solver — two levels

### 3.1 Block layout (zones)

- Discretise the buildable area into a grid (default 1 m; 0.5 m for small sheds).
- Each zone is a rectangle with integer width/height chosen from candidate shapes meeting `area ± 5%` and aspect limits.
- **CP-SAT** model: `NoOverlap2D` on zone intervals; inside boundary; column cells blocked (existing shed); fixed zones (docks on road side, utility yard on boundary) pinned; keep-apart constraints as minimum centroid/edge distances; main aisle corridors reserved (3 m forklift, 1.2 m pedestrian).
- Objective: minimise `Σ flow_ij × weight × manhattan(centroid_i, centroid_j)` + penalties for unmet E/I closeness + small compactness term.
- Time limit 60 s on 2 cores. Fallback: CRAFT-style pairwise exchange from a greedy initial placement. Result labelled `optimal | feasible | fallback`.
- **Pinning:** the user can pin zones and ask for "re-optimise around my edits".

### 3.2 Line layout (inside production zones)

- Line shape chosen per strategy and zone proportions: **I**, **U** (default for manual assembly; operator shares ends), or **L**.
- Stations placed in sequence with catalog footprints, operator space (min 1.0 m behind bench), conveyor or bench pitch, test positions grouped as racks, supermarket racks on the feed side.
- Material entry and exit points aligned with the block layout flows.

### 3.3 Greenfield vs existing shed

| Mode | Behaviour |
|---|---|
| Greenfield | Building footprint = Σ zone areas × (1 + circulation factor, default 0.25), rounded to a pre-engineered building grid (bay spans 20–30 m, column spacing 6–9 m from pack); site plan places building, admin block, parking, truck turning circle (12 m radius default), utility yard, green area, within setbacks and ground coverage |
| Existing shed | Boundary, columns, doors fixed. If Σ areas exceed usable area the solver reports the shortfall by zone and suggests mezzanine for offices/storage or a second shift (link to S5) |

### 3.4 Checks (server and browser)

No overlaps · inside boundary · aisle widths · keep-apart distances · exit travel distance (max from any point to an exit, pack value) · dock apron depth · clear height vs racking height · Li-ion store on an external wall · fire access path around building (greenfield).

The same check functions are implemented in Python (authoritative) and in TypeScript (instant feedback while dragging). A shared test-vector file guarantees both agree.

## 4. Outputs

`Layout { site, building, zones[], lines[], stations[], aisles[], doors[], exits[], metrics{flow_distance, area_utilisation, violations[]}, scene }`

- **2D:** SVG plan with layers (zones, equipment, aisles, flows as arrows with thickness = loads/day, dimensions).
- **DXF:** `ezdxf` export with layers per category, for architects and contractors.
- **3D scene graph** (`scene.json`): nodes with type, footprint, height, transform, material key, metadata id.

## 5. 3D viewer (React Three Fiber)

- Floor, walls, columns, roof outline (toggle), zone floors coloured by kind, aisle markings.
- Equipment as extruded footprints with heights from the catalog; optional GLB per machine class from a small free asset set; **instanced meshes** for racks, test channels, benches and pallets so hundreds of items stay fast.
- Operators as simple capsules at stations (count from roster, per shift toggle).
- Overlays: flow arrows, heatmap of utilisation (from S10), utility runs (simplified), hazard zones.
- Camera presets: overview, walk-through height, line view; orbit controls.
- Click any object → side panel: details (machines, operators, kW, air, KPIs, provenance).
- Performance target: ≥ 30 fps for the golden layouts on a mid-range laptop; ≤ 2,000 draw calls via instancing.

## 6. Editing

- Drag zones or stations on a 2D/3D grid with snapping; rotate 90°; resize zones within area tolerance.
- Live browser checks show violations as you move; save runs server checks and creates a new Layout version (`source=user` for moved objects).
- "Re-optimise around pinned objects" calls the solver with pins.
- Any saved edit marks S10 (simulation) and S11 (finance, for area-driven costs) stale.

## 7. Engine tests

- Tiny QAP instance (4–5 zones) — solver result equals brute-force optimum.
- Golden layouts: zero violations; flow distance below the greedy baseline.
- Existing-shed case with columns: no zone covers a column cell; shortfall reported correctly when areas exceed the shed.
- Python/TS check parity on shared vectors.
