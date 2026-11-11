# 03 — Product template, intake and BoM

## 1. The product template

One standard, versioned schema (`ProductTemplate v1`) defined in Pydantic. JSON Schema is generated from it and drives the web wizard and the TypeScript types. The POC has one entry route: the **web wizard** (ADR-015). Excel upload is a later option and must map to the same schema.

### 1.1 Field tiers

| Tier | Fields | Rule |
|---|---|---|
| Required | product name; category (`electronics` / `mechanical`); short description; site state and district (Karnataka in POC); site mode (`greenfield` or `existing_shed`); at least one of: CAD file, photo, BoM file, written description | Wizard cannot submit without them |
| Recommended | variants and mix; annual volume; selling price per variant; budget; plot size or shed dimensions | Agent suggests a value if blank; marked `llm` |
| Optional | everything else (targets, finance inputs, extensions) | Agent or defaults fill; user may override |

### 1.2 Schema outline

```yaml
template_version: 1
product:
  name: str
  category: electronics | mechanical
  archetype: power_bank | ceiling_fan | generic_electronics | generic_mechanical   # set by intake
  description: str
  variants:                       # 1..n (POC: 2)
    - code: str                   # e.g. PB10K
      name: str
      mix_pct: float              # sums to 100
      selling_price_inr: float?
      differences: str            # free text; intake turns into structured deltas
  demand:
    annual_volume: int?           # all variants
    ramp_plan: [ {year: int, pct_of_target: float} ]?
  targets:
    unit_cost_inr: float?
    quality_level: basic | standard | premium
    automation_level: manual | semi | high
site:
  state: KA
  district: str
  mode: greenfield | existing_shed
  plot: { length_m, width_m, road_side: N|E|S|W }?          # greenfield
  shed: { length_m, width_m, clear_height_m, column_grid_m: [x, y], doors: [...] }?   # existing
operating_pattern:                # captured in S5, defaults shown in wizard
  shifts_per_day: 1
  days_per_week: 6
  target_oee: 0.85
finance_inputs:
  budget_inr: float?
  land_cost_inr_per_sqm: float?   # agent suggests per district if blank
  debt_equity: [70, 30]
  interest_rate_pct: float?
  loan_tenure_years: int?
  discount_rate_pct: 12
  horizon_years: 5
attachments: [ { id, kind: step|stl|photo|pdf|bom_csv|text, file_ref, note } ]
structure:                        # filled by S1/S2
  assemblies: tree
  parts: [ { part_no, name, qty, material, mass_g, bbox_mm, process_hint, make_or_buy } ]
extensions:
  electronics: { cell_chemistry, cell_format, cells_per_pack, pcb_count, ports, test_spec }
  mechanical:  { fasteners, joining, surface_finish, critical_tolerances, motor_type }
```

### 1.3 Provenance wrapper

Every leaf value that is not a pure identifier is stored as:

```json
{ "value": 10000, "unit": "mAh", "source": "datasheet", "confidence": 0.9,
  "ref": "attachment:att_03#page2", "note": "Rated capacity from cell datasheet" }
```

`source` ∈ `user | cad | datasheet | vision | llm | library | web | engine`. Units are normalised with `pint`.

## 2. Web wizard

Steps: 1 Product → 2 Variants & volume → 3 Files → 4 Site → 5 Money (optional) → Review.

- Tooltips explain each field in plain language with an example.
- "Load sample" fills the power bank or fan example.
- Required fields block; recommended fields show "We will suggest this".
- Files upload directly to the API (type and size checks, doc 11).
- Submit creates the project, returns its capability link, and starts S1.

## 3. Stage S1 — Intake

### 3.1 File processors (worker, sandboxed)

| Input | Processor | Extracted | Confidence |
|---|---|---|---|
| STEP / STP | CadQuery/OCP | Assembly tree, part names, solids, bounding boxes, volumes, surface areas; mass = volume × density once material known; GLB for viewer; thumbnails | High for geometry; names as authored |
| STL | trimesh | Single mesh bbox, volume, area | Low (no assembly) |
| Photos (jpg/png/webp) | `vision` role | Candidate components, visible features (ports, LEDs, blades, canopy), approximate size cues | Low (≤ 0.4) |
| PDF (datasheets, drawings) | pdfplumber → `extractor` role | Specs with units (mAh, V, W, RPM, sweep, ports) | Medium–high with page reference |
| BoM CSV / XLSX | Column mapper → normaliser | Lines (handled in S2) | High |
| Wizard text | `extractor` role | Structured fields | Medium |

STEP files are processed in a separate container with CPU/memory/time limits (60 s, 2 GB) because OCP is heavy and files are untrusted.

### 3.2 Merge rules

1. Collect candidate values per field from all sources.
2. Rank by source priority: `user > cad > datasheet > bom_file > vision > llm`.
3. Units converted; numeric values within 5% are treated as agreeing.
4. Disagreement above tolerance between two sources with confidence ≥ 0.5 → a **question** to the user showing both values and sources.
5. Archetype classification (`extractor` + rules): keywords, extension fields, and CAD part names. Unknown → `generic_*` with a notice.

### 3.3 Output and gate

`ProductSpec` (validated template with provenance) + question list. The user answers questions and approves. Approval unlocks S2.

## 4. Stage S2 — BoM

### 4.1 Two routes

- **User BoM supplied:** map columns, normalise units and part numbers, detect duplicates and missing quantities, enrich each line with material, commodity class (from catalog taxonomy), and a make/buy suggestion.
- **No BoM:** start from the archetype skeleton (library, doc 10), then the `reasoner` fills specifics from the spec as structured output. Each line has confidence; skeleton lines not justified by the spec are dropped or questioned.

Power bank skeleton (variant deltas in brackets): Li-ion cells (2 / 4 × 21700 or pouch) · BMS + charge PCBA · USB-C and USB-A ports (on PCBA) · enclosure top/bottom (PC/ABS, molded, bought) · LED light pipe · button · nickel strip · insulation (fish paper, Kapton) · foam pads · screws · rating label · QR label · user manual · retail box · shipper carton · consumables (solder wire, flux, adhesive).

BLDC fan skeleton: stator core (laminations) · copper winding wire · insulation and varnish · rotor/outer shell (die-cast, bought) · magnets (in rotor, bought as rotor assembly) · shaft and bearings · BLDC controller PCBA (bought) · remote + receiver (bought) · blades ×3 (1200 / 900 mm, bought) · canopy, down-rod, shackle, mounting kit · screws, washers, split pins · wiring harness · rating and BEE label · manual · carton · consumables.

### 4.2 EBOM → MBOM

Engine transform:

- group lines into sub-assemblies by how they are built (e.g. `cell pack`, `motor`, `final assembly`);
- add consumables per process from the catalog (solder, flux, nickel strip, varnish, adhesive);
- add packaging levels (unit box, inner, shipper);
- keep a link from each MBOM line to its EBOM line.

### 4.3 Make / buy (separate approval)

Each line gets a recommendation with a preview of its effect:

| Line | Default | Preview shown |
|---|---|---|
| Power bank PCBA | Buy | In-house SMT would add ₹50 lakh–1.5 crore capex, ~120 m² ESD area, 4–6 operators per shift (catalog ranges) |
| Cells | Buy | — |
| Enclosure | Buy (molded) | In-house molding would add machines, tooling and a regrind area |
| Fan stator winding | Make | Winding machines, varnish oven, testers |
| Fan rotor / shell, blades | Buy | — |
| Fan controller PCBA | Buy | As PCBA above |

The preview is computed by a lightweight version of S5/S8/S11 using catalog data only. The user confirms each decision; this is the first approval with large cost impact.

### 4.4 Outputs

`Bom { ebom, mbom, make_buy_decisions, commodity_classes }`. On approval, the vendor agent starts in parallel (doc 09 §2) using commodity classes and make/buy decisions.

### 4.5 Validators

- Every variant has a complete MBOM; quantities > 0; units valid.
- Every "make" item has at least one candidate process in the catalog or is flagged `uncatalogued`.
- Every commodity class exists in the catalog taxonomy.
- Variant deltas reference existing lines (no orphan variant parts).
