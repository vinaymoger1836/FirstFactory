# 06 — Inventory, work orders, shifts and utilities

## 1. Stage S6 — Inventory and material flow

### 1.1 Stock policy per MBOM item

- **ABC** by annual consumption value (A ≥ 80%, B next 15%, C rest).
- **Lead time** from the vendor report when available, otherwise the commodity-class default in the catalog (imported cells much longer than local molded parts); source shown.
- **Safety stock** `SS = z × σ_d × √LT` with service level per class (A 95%, B 97.5%, C 99% → z), `σ_d` from an assumed demand CV (default 0.2, editable).
- **Reorder point** `ROP = d × LT + SS`; **cycle stock** from order quantity (EOQ or MOQ, whichever larger); **days of cover** reported.
- Finished goods: days of cover (default 7) × daily output.

### 1.2 Storage sizing

```
units → cartons (pack qty) → pallets (cartons per pallet) or bins → rack positions → m² (with aisles)
```

| Storage class | Used for | Rules |
|---|---|---|
| General racking | Molded parts, hardware, packaging | Selective pallet racking or shelving by item size |
| ESD storage | PCBAs | ESD shelving, ESD floor zone |
| Li-ion hazardous store | Cells, finished power banks awaiting dispatch | Segregated, fire-rated enclosure, quantity limit per compartment, detection + suppression, distance from exits and canteen (layout constraint) |
| Flammables | Varnish, solvents | Separate small store, ventilation |
| Quarantine / MRB | IQC holds, rejects | Sized from lots on hold |
| Line-side supermarkets | Kits per station | From replenishment interval × consumption |
| FG warehouse | Packed goods | Days of cover |
| Docks | Receiving and dispatch | Bays from truck visits/day and unload time |

Material handling equipment (hand pallet trucks, stackers, trolleys, totes) counted from moves per day (flow matrix) and move time.

### 1.3 Outputs

`InventoryDesign { policies[], zones[], flow_matrix[], mhe[] }` — zones carry kind, area, aspect limits, hazards and utility needs; the flow matrix (loads/day between zones) drives the layout objective.

## 2. Stage S7 — Work orders and shifts

### 2.1 Work-order design

The platform designs the work-order system, not an MES.

- **WO template:** header (WO no., variant, quantity, due date, routing version, BoM version), operation list from routing, kit list from MBOM, quality checkpoints, labels to print.
- **WO types:** production, rework, lab sample, changeover.
- **Traceability:** power bank — cell lot → pack serial → unit QR; fan — motor serial → unit QR. Serial format and label content defined.
- **Release logic** per chosen strategy: mixed-model → daily levelled sequence (e.g. PB `10K,20K,10K,10K,20K` repeating for 60/40); dedicated → per-line daily quantity; batch → batch sizes and changeover calendar.
- **Sample week plan** (WOs for one week at target volume) and **draft work instructions** per station written by the `writer` role from the station's tasks, tools and checks — clearly marked "draft, to be validated on the shop floor".

### 2.2 Shifts and roster

Built on the operating pattern from S5.

- Direct operators = Σ station operators per shift.
- Relievers = absenteeism allowance (default 9%) + break cover if the line does not stop for breaks.
- Supervisors at 1:20–25 direct; team leaders per line.
- Indirect staff by ratio (catalog defaults, editable): material handlers (from MHE moves), QC inspectors (from S4 hours), maintenance (per machine count), stores, planning, admin, security, housekeeping.
- **Compliance check** against the Karnataka pack (versioned, dated): maximum daily and weekly hours, overtime cap per quarter, weekly rest day, spread-over, conditions for night shifts for women, leave accrual. Violations block approval with an explanation.
- **Welfare facilities** by headcount thresholds from the pack (canteen, creche, first aid room, rest rooms, washrooms, drinking water) → zones for the layout.
- **Labour cost** = headcount by grade × wage from pack (minimum wage as floor; user can set market wage) × statutory loading (PF, ESI, bonus, gratuity provision) → finance.

Outputs: `WorkOrderDesign`, `Roster { shifts, headcount_by_role_shift, rota_template, compliance_report, welfare_zones, labour_cost }`.

## 3. Stage S8 — Utilities

### 3.1 Electrical

```
connected_kW   = Σ equipment kW (catalog) + lighting (W/m² by zone kind × area)
               + HVAC + compressors + IT/office + test-rack draw (net of regeneration)
demand_kW      = Σ group (connected × demand_factor) × diversity_factor
max_demand_kVA = demand_kW / power_factor_target (0.95 with APFC)
contract_kVA   = round_up(max_demand_kVA × (1 + growth_margin 20%))
supply         = LT or HT by the pack's threshold (BESCOM; value marked "verify")
transformer    = next standard rating ≥ contract_kVA × 1.25 (if HT)   # 100,160,200,250,315,400,500,630,1000…
DG set         = critical loads (or full) × 1.25, next standard rating
UPS            = test systems, servers, controls
energy_kWh/yr  = Σ (kW × load_factor × operating_hours)
```

Golden check: max demand 180 kVA → × 1.25 = 225 → **250 kVA** transformer.

Tariff from the Karnataka pack with effective dates (e.g. HT-2a industrial demand charge ₹/kVA/month + energy ₹/kWh, plus applicable surcharges/taxes as configured) → annual power cost to finance. Rooftop solar potential = usable roof area × kWp/m² (pack) with expected generation; reported as an option, not in the base case.

### 3.2 Compressed air

`demand_Nl_min = Σ tool consumption × duty × diversity + leakage (15%)` → compressor kW (catalog specific power), receiver volume, dryer, ring-main hint.

### 3.3 HVAC and ventilation

- Cooling/ventilation per zone kind: assembly (comfort ventilation or evaporative cooling), ESD/test zones (cooling), offices (AC).
- Heat load = equipment heat (kW × heat fraction; **battery test racks dissipate most of the discharged energy as heat** unless regenerative) + people + lighting + envelope (W/m² from pack climate zone).
- Exhaust: varnish oven, soldering fume extraction (air changes per hour from catalog).
- Output: TR per zone, exhaust units, AHU/ventilation list.

### 3.4 Water

Domestic (litres/person/day × headcount), process (catalog), fire-water tank (from fire norms in pack), rainwater harvesting (roof area, pack rule).

### 3.5 Fire and safety

Detection/suppression for Li-ion store and test area, extinguishers by area/class, hydrant/sprinkler if thresholds met (pack), emergency exits feed the layout's travel-distance check. Fire NOC listed in compliance.

### 3.6 Outputs

`UtilityDesign { electrical{connected, MD, CD, supply, transformer, DG, UPS, APFC, kWh, cost}, air{}, hvac{}, water{}, fire{}, rooms[] }` — rooms (transformer yard, LT panel room, DG yard, compressor room, water tanks, fire pump) become layout zones with constraints (yard on site boundary, DG away from offices, heavy loads near LT room).
