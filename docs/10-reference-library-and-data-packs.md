# 10 — Reference library and data packs

The platform's accuracy depends on this data more than on any model. It is versioned, sourced, tested and editable through the admin page.

## 1. Process catalog

Stored as YAML in the repo (`library/catalog/`), loaded into Postgres on deploy, indexed with pgvector for search. Version string `YYYY.MM.patch`; projects pin the version they were created with (ADR-009).

### 1.1 Entry kinds

| Kind | Examples |
|---|---|
| `process` | spot_weld_nickel, manual_solder, ultrasonic_weld, screw_drive, stator_winding, varnish_dip_cure, dynamic_balance, smt_place, reflow, label_apply, pack_box |
| `machine_class` | spot_welder_ac, winding_machine_2spindle, cure_oven_batch, hipot_tester, battery_tester_channel, functional_test_fixture, screwdriver_electric |
| `check_type` | cell_ocv_ir, pack_voltage_ir, hipot, winding_resistance, functional_pb, functional_fan, capacity_test, run_in_test, drop_test |
| `zone_kind` | assembly_line, esd_assembly, test_area, li_ion_store, flammables_store, rack_storage, fg_warehouse, dock, lab, canteen, office, utility_yard |
| `commodity_class` | li_ion_cell, pcba, molded_enclosure, die_cast_rotor, fan_blade, fasteners, packaging_carton, consumable_solder |
| `archetype` | power_bank, ceiling_fan (BoM skeleton, typical routing, compliance refs, variant deltas) |
| `mtm_element` | reach, grasp, place, insert_snug, screw_start, label_apply, inspect_visual |

### 1.2 Process entry schema

```yaml
code: spot_weld_nickel
kind: process
name: Spot welding of nickel strip to cylindrical cells
aliases: [battery spot welding, tab welding]
applies_to: { archetype: [power_bank, generic_electronics], commodity: [li_ion_cell] }
machine_class: spot_welder_ac
operators_per_machine: 1
skill: semi_skilled
time_model:
  formula: "n_welds * 1.5 + 8"          # seconds; variables resolved from product params
  variables: { n_welds: "cells_per_pack * 4" }
  distribution: triangular
  spread_pct: 15
setup_s: 300
fpy: 0.985
rework: { op: rework_weld, time_s: 90 }
consumables: [ { commodity: nickel_strip, qty_expr: "cells_per_pack * 0.06", uom: m } ]
quality_checks: [pack_voltage_ir]
utilities: { kw: 3.0, demand_factor: 0.4, air_nl_min: 0, exhaust: false }
footprint_m2: 1.8          # incl. operator space
height_m: 1.6
zone_kind: esd_assembly
hazards: [li_ion]
price_inr: { low: 60000, base: 120000, high: 250000, as_of: 2026-11, source: "catalog-estimate" }
mtbf_h: 400
mttr_h: 1.5
notes: "Capacitor-discharge welders preferred for thin nickel; check strip thickness."
sources: []                 # URLs when values come from public data
```

Every numeric field may carry `source`, `as_of`, `confidence`. Catalog estimates are labelled as such and shown with lower confidence in projects.

### 1.3 Seed scope for POC

About 30–40 processes, 25 machine classes, 15 check types, 15 zone kinds, 20 commodity classes, 2 archetypes, ~40 MTM elements — enough to fully cover both golden products plus common generic steps (SMT, reflow, molding) for future products.

### 1.4 Catalog tests (CI)

- Schema validation for every entry.
- Every formula evaluates with the golden product parameters; units consistent.
- Every reference (machine_class, check, commodity, zone_kind) resolves.
- Golden routings can be built using only catalog entries.

## 2. Regional data packs

One pack per state: `library/packs/KA/` (Karnataka first). Every value has `effective_from`, `effective_to?`, `source_url`, `retrieved_at`, `confidence`, and `verify: bool` when unconfirmed.

### 2.1 Karnataka pack contents

| Group | Keys (examples) | Notes |
|---|---|---|
| Labour (OSH Code + central/state rules) | daily_hours_max, weekly_hours_max, overtime_cap_quarter_h, spread_over_max_h, weekly_rest_days, night_shift_women_conditions, leave_accrual | Central rules notified 2026; state rules evolving → re-check each quarter |
| Welfare thresholds | canteen_min_workers, creche_min_workers, first_aid_ratio, washroom_ratio | |
| Wages | minimum wage by skill category and zone; statutory loading (PF, ESI, bonus, gratuity provision) | Revised periodically |
| Power tariff | supply category thresholds (LT/HT for BESCOM — `verify`), HT-2a demand charge ₹/kVA/month, energy ₹/kWh, LT-5 rates, ToD rules, taxes/surcharges | Tariff orders change mid-cycle; keep history |
| Building | setbacks by plot size, ground coverage, FAR, parking norms, exit travel distance, PEB ₹/m² ranges, civil ₹/m² | Industrial area rules vary by authority → `verify` |
| Fire | thresholds for hydrant/sprinkler, fire-water tank sizing | |
| Water | litres/person/day, rainwater harvesting rule | |
| Climate | design temperatures, envelope W/m² defaults | For HVAC |
| Compliance by archetype | power bank: BIS CRS (finished product; cells separately; capacity verification); ceiling fan: Electric Ceiling Type Fans QCO 2023 + mandatory BEE star label | Informational, dated |
| Land | indicative ₹/m² by district (web-sourced, low confidence) | Agent suggestion only |

### 2.2 Initial sources (to be re-verified at P1)

- Labour Codes in force 21 Nov 2025; OSH (Central) Rules 2026 notified 8 May 2026 (48 h/week, overtime at 2× wages, overtime cap 144 h/quarter). — e.g. https://www.sansalegal.com/post/oshwc-central-rules-2026-india-notifies-final-occupational-safety-framework
- KERC tariff revision 2026: HT-2a industrial demand charge ₹365/kVA and energy ₹6.70/unit. — https://theprint.in/india/kerc-increases-electricity-tariffs-for-commercial-industrial-consumers/2869823
- Power bank CRS and cell registration: https://www.compliancecalendar.in/learn/selling-power-banks-bis-crs-certification-rules
- Cell rated-capacity verification: https://admin.corpseed.com/knowledge-centre/bis-issues-new-guidelines-for-verification-of-rated-capacity-of-lithium-cells-and-batteries
- Ceiling fans QCO 2023: https://taxguru.in/?p=880889 ; BEE star label for BLDC and induction fans: https://bis.alephindia.in/latest-update/new-compliance-for-electric-ceiling-fan-the-minist/292

Primary sources (gazette notifications, KERC tariff order PDF, BIS/BEE pages) replace secondary ones during P1 where reachable.

### 2.3 Pack tests

- Schema; every value has source + effective date; no expired value used without a newer one.
- `verify: true` values appear in the report's assumptions register automatically.

## 3. Admin page

A small section of the web app at `/admin`, protected by Cloudflare Access (free tier) **and** an admin passcode checked by the API (defence in depth; doc 11).

- Browse/search catalog and packs; schema-driven forms (same JSON Schema as YAML).
- Edit creates a **draft version**; "validate" runs the catalog/pack tests server-side; "publish" creates a new version (`2026.11.2`), writes YAML back to a git-tracked data directory on the server and records an audit entry. (The repo YAML remains the seed; a nightly job can export published versions for committing.)
- Diff view between versions.
- Projects keep their pinned versions; a project owner can choose "upgrade library version", which marks affected stages stale.
- Test bench: run the capacity and utility engines on a golden product with the draft version to see the effect before publishing.
