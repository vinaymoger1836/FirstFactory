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
attachments: [ { id, kind: step|stl|photo|pdf|bom_csv|text, file_ref, note, bom_columns? } ]   # bom_columns: confirmed column mapping (P2)
bom_hint: have_full | have_partial | none        # wizard question "Do you have a BoM?"; a hint only, S2 still checks the file
parts_paste: [ { name, qty?, note? } ]?          # optional parts typed or pasted in the wizard (no file)
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

`source` ∈ `user | cad | datasheet | bom_file | vision | llm | library | web | engine` (`bom_file` was added in P1; it sits between `datasheet` and `vision` in the merge order). Units are normalised with `pint`.

## 2. Web wizard

Steps: 1 Product → 2 Variants & volume → 3 Files and parts → 4 Site → 5 Money (optional) → Review.

- Step 3 asks **"Do you have a BoM?"** with three answers: *Yes, I will upload it* · *I have part of it* · *No, draft one for me*. The answer sets `bom_hint`. It never blocks submission and it does not change what S2 verifies. Step 3 also takes CAD, photos and datasheets, and an optional parts table (typed or pasted) for users with a short list but no file.
- After a BoM file is uploaded the wizard shows its column mapping (proposed by the `extractor` role, rules first) for the user to confirm or fix before submit; an unmapped quantity column is shown as a warning.
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
| BoM CSV / XLSX | Column mapper (confirmed in the wizard) → normaliser | Raw lines kept as `bom_file` candidates in `structure.parts`; cleaning, enrichment and gap checks happen in S2 (§4) | High for lines the user wrote |
| Pasted parts table | `extractor` role | Candidate lines without part numbers | Medium |
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

### 4.1 Three cases

S2 starts only after the S1 `ProductSpec` is approved. It picks its route from what S1 actually found, not from `bom_hint`:

| Case | What the user gave | S2 does |
|---|---|---|
| **A. CAD only** (or photos, or description only) | STEP/STL, photos, text; no BoM lines | Start from the archetype skeleton (§4.2). Match CAD parts to skeleton lines by part name and geometry (`source=cad`, high confidence for what CAD shows). The `reasoner` drafts the lines CAD cannot show (cells, PCBA, labels, manual, packaging, consumables) from the spec as structured output (`source=llm`). Skeleton lines the spec does not justify are dropped or turned into questions. Unknown archetype → `generic_*` skeleton, a notice, and many more questions. |
| **B. Complete BoM** | CSV/XLSX that covers the product | Confirm column mapping; normalise units and part numbers; detect duplicates, missing or zero quantities; enrich each line with material, commodity class and a make/buy suggestion. **The user's lines are never rewritten.** Gap check (§4.6) runs anyway, because a complete engineering BoM usually has no consumables, packaging or sub-assembly grouping; those are added in the MBOM step (§4.4). |
| **C. Incomplete BoM** | A BoM missing parts, quantities or variant columns | Everything in B for the lines given. Then the gap check (§4.6) compares the BoM with the archetype skeleton and with CAD, and lists probable missing parts as **suggestions** for the user to accept or reject. Nothing is added silently. |

Cases combine: a BoM plus CAD runs B or C with the CAD cross-check (§4.6) on top. Photos and datasheets only raise or lower confidence and add questions.

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

The BoM is the centre of the pipeline: routing, quality plan, capacity, inventory, vendors, work instructions and cost all read the approved MBOM. That is why S2 has the strictest gate (§4.5) and why every line shows where it came from (§4.7).

### 4.5 Validators

- Every variant has a complete MBOM; quantities > 0; units valid.
- Every "make" item has at least one candidate process in the catalog or is flagged `uncatalogued`.
- Every commodity class exists in the catalog taxonomy.
- Variant deltas reference existing lines (no orphan variant parts).
- **No line is left in review status `suggested` at approval** (accepted or rejected first, §4.7).
- A line with `origin=user_bom` is unchanged from the file except for normalisation (units, part-number format); any other edit is recorded as a user edit.

### 4.6 Gap check and CAD cross-check

Runs in every case after cleaning, before the EBOM is shown. All findings are **suggestions or questions**, never silent changes.

1. **Against the archetype skeleton, by commodity class, not by name.** If the BoM has no line in a class the skeleton expects (for example `label`, `user_manual`, `packaging_carton`, `consumable_solder`), propose one line for that class. Class-based matching avoids false gaps when the user names a part differently. Rejected suggestions are remembered for the project and are not proposed again on a re-run.
2. **Against CAD (when STEP is attached).**
   - A CAD part with no BoM match (`part_no` equal, else name similarity plus bounding box within 5%) → "CAD shows *X*, not in your BoM. Add it?"
   - A quantity that differs (CAD has 4 screw solids, BoM says 2) → a question showing both values and sources (the merge rule in §3.2).
   - BoM lines with no CAD match are **not** flagged when their class is something CAD rarely models (cells, ICs, labels, packaging, consumables, fasteners); other unmatched lines get an information note.
3. **Line quality.** Missing or zero quantity, unknown unit, duplicate part numbers, a variant column that names a variant not in the template → a question per issue, grouped.
4. Findings are sorted by effect (missing parts first, then quantity conflicts, then notes) so the review screen starts with what matters.

### 4.7 Line origin and review status

Each BoM line carries two fields in addition to `Prov`:

| Field | Values | Purpose |
|---|---|---|
| `origin` | `user_bom` · `cad` · `skeleton` · `reasoner` · `engine` | Where the line came from. `engine` = added by the EBOM→MBOM step (consumables, packaging, grouping). |
| `review` | `given` (user supplied) · `suggested` · `accepted` · `rejected` | `suggested` lines must be resolved before approval. Accepted suggestions keep their origin and stay visible in the assumptions list. |

The S2 screen is an **editable table**: filter by origin, review status or confidence; edit a quantity or part inline (the edit sets `source=user`, confidence 1.0); accept or reject suggestions in bulk. Low-confidence lines sort first.

### 4.8 BoM workbook (export)

One XLSX (openpyxl), included in the report pack (doc 09 §3):

| Sheet | Content |
|---|---|
| EBOM | Part, description, quantity per variant, material, commodity class, origin, confidence |
| MBOM | EBOM lines plus consumables, packaging and sub-assembly grouping; `ebom_ref` link |
| Make-Buy | Decision per line, catalog-based cost, area and headcount preview, approval |
| Variants | Lines that differ between variants |
| Open items | Lines still below confidence 0.6, or accepted from AI suggestions, for the assumptions register |

The EBOM is the design view; the MBOM is what purchasing, work orders and work instructions use. Both are delivered.

### 4.9 Implementation notes (after P1)

**As built in P3a (2026-11-16):** `BomLine.origin`, `BomLine.review`, `BomLine.id`, `BomLine.commodity_class`, `BomFinding`, `Bom.case`, `Bom.user_snapshot`, `Bom.rejected_suggestions`, `Bom.coverage`, `Bom.totals`, `MakeBuyDecision.confirmed`; validators "user lines unchanged" and (at approval) "no suggested line, no open finding, every make/buy confirmed". The S2 engine is `fl_engines.bom.build_bom`; the proposer is a tool loop (ADR-026); review decisions are sent as one "apply review" action and create a new version.

P1 already provides the `Bom` model and validators (refs, qty > 0, cycles, complete MBOM per variant, commodity classes, make items routable or `uncatalogued`) and `Prov.source=bom_file`. The additions in §4.6–4.7 are **additive model changes** done in P3: `BomLine.origin`, `BomLine.review`, a `BomFinding` type for suggestions and questions, and the two validators in §4.5. `bom_hint` and `parts_paste` are added to `ProductTemplate` in P2 (wizard). Both need a regenerated JSON Schema and TS types, and no stored projects exist yet, so no data migration is needed.
