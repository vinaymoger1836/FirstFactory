# 09 — Finance, vendor research and reports

## 1. Stage S11 — Finance

All calculations are deterministic Python (`numpy-financial`). Every figure is a range: **low / base / high**, from catalog price ranges and vendor evidence.

### 1.1 Capex

| Group | Source |
|---|---|
| Production machines and fixtures | S5 equipment list × catalog price ranges (vendor quotes override when found) |
| Test equipment and racks | S5 test positions/channels × catalog |
| Quality lab equipment | S4 |
| Material handling, racking, bins | S6 |
| IT, labelling, traceability hardware | S7 |
| Building — greenfield | Land (user input or agent-suggested per district, marked `llm/web`) + pre-engineered building ₹/m² + civil works + ESD/epoxy flooring for ESD zones + offices fit-out (pack rates) |
| Building — existing shed | Fit-out, flooring, partitions, deposit (rent goes to opex) |
| Utilities infrastructure | S8: transformer, HT/LT panels, DG, UPS, APFC, compressor, HVAC, fire system, water tanks |
| Certification and approvals | BIS/BEE testing and fees, factory approvals (pack, informational) |
| Pre-operative expenses | Default % of capex (editable) |
| Contingency | Default 10% |

### 1.2 Working capital

Inventory value (S6 days of cover × consumption) + receivables (default 30 days of sales) − payables (default 30 days of purchases).

### 1.3 Opex (annual)

Materials (MBOM × unit prices per variant; vendor evidence or catalog range) · direct and indirect labour (S7) · power (S8: demand charges + energy) · consumables · maintenance (% of equipment capex) · rent (existing shed) · insurance (% of assets) · warranty provision (% of sales) · logistics (% of sales) · admin overheads.

### 1.4 Results

- Unit cost per variant (material + conversion) and contribution margin.
- 5-year P&L and cash flow with ramp-up (template ramp plan or default 60% / 85% / 100% …).
- Depreciation (straight line, configurable lives per asset group).
- Financing: debt:equity 70:30 default, interest rate and tenure from inputs; EMI schedule; DSCR.
- Break-even volume (units/year), payback period, **NPV** at 12% default, **IRR**.
- Sensitivity: tornado on selling price, volume, material cost, capex, labour cost (±10/20%).
- Incentives under Karnataka industrial policy: listed for information from the pack/research, **not in the base case**.

Golden check: cash flows `[-100, 30, 30, 30, 30, 30]` at 12% → NPV **8.14**, IRR **15.24%**.

## 2. Vendor research (parallel stage P1)

Starts when the BoM (and make/buy) is approved; refreshes when they change.

### 2.1 Scope

1. BoM commodity classes (cells, PCBAs, enclosures, rotors, blades, packaging, consumables).
2. Machine and equipment classes from S5/S8 (spot welders, winding machines, testers, ovens, compressors, transformers, DG sets, racking).
3. Construction: pre-engineered building suppliers and civil contractors (Karnataka focus).

### 2.2 Rules

- **Public web only:** search API results and publicly reachable pages; respect robots.txt; no scraping behind logins; polite rate limits.
- **No invented vendors:** every vendor record must have at least one source URL fetched in this run or from the cache; the extractor quotes the supporting snippet internally; records without evidence are dropped.
- Extract: name, location (city/state), products relevant to the class, certifications claimed (e.g. BIS registration for cells), MOQ, indicative price and lead time if public, contact page URL. Missing fields stay empty.
- Score (0–100): relevance to the class and spec, distance to site, certifications, evidence quality (manufacturer site > directory listing), recency.
- Deduplicate by domain and name similarity.
- **Shared cache** across projects (`vendor_cache`, TTL 30 days) keyed by commodity class + region, to save quota.
- Price and lead-time facts flow to S6 and S11 as `source=web`, confidence ≤ 0.6.

### 2.3 Output

`VendorReport { classes: [ { class, vendors[≤8], notes, queries_run, gaps } ] }` with the disclaimer "leads, not endorsements; verify before purchase".

## 3. Stage S12 — Reports

### 3.1 Report sections

1. Executive summary (one page: what to build, capex range, headcount, area, power, unit cost, payback)
2. Product and BoM (EBOM, MBOM, make/buy)
3. Process and line design (routing, the three strategies, chosen line)
4. Quality plan and compliance checklist (BIS CRS, BEE/QCO, labour, Fire NOC, pollution-control consent category — to be confirmed)
5. Inventory and warehouse
6. Work orders, shifts and workforce
7. Utilities
8. Layout (site plan, building, 2D plan, 3D snapshots)
9. Simulation results and applied improvements
10. Financials
11. Vendors
12. Assumptions and risks register (every value with confidence < 0.6 or `source ∈ {llm, web}`)
13. Appendix: SysML v2 model summary, catalog and pack versions, disclaimer

### 3.2 Formats

| Format | Tool |
|---|---|
| Web report (interactive) | React pages from artifacts |
| PDF | Jinja2 → HTML → WeasyPrint |
| XLSX workbook (BoM, routing, stations, policies, roster, utilities, capex, P&L, vendors) | openpyxl |
| DXF layout | ezdxf |
| SysML v2 `.sysml` | Own serializer (doc 04 §6) |
| Full project export (ZIP: JSON artifacts + files + reports) | API |

Exports always name artifact versions, catalog version and pack versions so a report can be reproduced.
