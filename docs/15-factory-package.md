# 15 — Factory Package: documents for building and running the factory

The platform's final output is not only reports about a design. It is the set of documents an entrepreneur needs to **build** the factory and then **run** it. This doc defines that set, which stage owns each document, how each is generated without breaking ADR-001 (engines calculate, LLM drafts words), and where it is built in the phase plan.

All documents are **planning-grade drafts**. Work instructions and inspection sheets are written without seeing the real shop floor, so each carries the label *Draft — validate on the shop floor before use* and a blank sign-off block.

## 1. The three sets

| Set | Contents | Owner stages |
|---|---|---|
| **Build** | 3D layout, 2D plan and DXF, site plan, utilities sizing, **equipment list with specifications for quotes**, vendor report, building brief | S5, S8, S9, vendors, S12 |
| **Run** | Work orders and sample week, roster, inventory policies, **work instruction per station**, **inspection instruction and check sheet per quality check**, **process flow chart**, **control plan**, **SOP set**, **training matrix** | S4, S6, S7, S12 |
| **Business** | Financials, compliance checklist, assumptions and risk register, simulation results | S4, S10, S11, S12 |

Bold items are new in this doc. The rest are already designed in docs 05–09.

## 2. Document types

### 2.1 Tier 1 (POC scope)

| Document | One per | Filled by engines from | Written by `writer` role |
|---|---|---|---|
| **Work instruction (WI)** | Station (per variant where steps differ) | Station id, operations and times (S3/S5), BoM lines used and quantities (S2), machines and tools (catalog), quality checks at the station (S4), hazards (ESD, Li-ion, hot surface; catalog zone/hazard data), layout zone (S9) | Step wording only |
| **Inspection instruction and check sheet** | Check in the quality plan (IQC per commodity class, first-article, patrol, 100% inline, EOL, OQC, lab) | Characteristic, limit, method, instrument class, sample size (AQL table), frequency, accept/reject rule, certification link (S4 and pack) | Short plain-language method text |
| **Process flow chart** | Variant | Routing and precedence graph | none (rendered diagram) |
| **Control plan** | Variant | Table compiled from routing + quality plan: operation, characteristic, limit, method, sample, frequency, reaction | Reaction wording only |
| **SOP set** | Topic (see §2.3) | Parameters from S4/S6/S7/S8 (e.g. store quantity limits, inspection levels) | Template text with parameters filled |
| **Training matrix** | Project | Roles from the roster (S7) × stations/checks/SOPs | none |
| **Equipment list with specifications** | Project | S5/S8 equipment, catalog machine classes (power, footprint, utilities, price range), vendor leads | Short purpose line per item |

### 2.2 Tier 2 (after the POC)

Process FMEA draft (the LLM would propose failure modes; severity, occurrence and detection ratings are judgement values, so they would be `source=llm`, low confidence, and the RPN computed by the engine), preventive-maintenance schedule (from MTBF/MTTR), calibration register, emergency and EHS plan, commissioning checklist, supplier quality agreement templates.

### 2.3 SOP topics (Tier 1)

Receiving and IQC · Material storage, including the Li-ion and flammables stores · Non-conformance, quarantine, MRB and rework · Gauge and instrument calibration · ESD control · Traceability and recall · Basic preventive maintenance. Each is a fixed template with parameter slots (limits, areas, intervals) filled from the approved artifacts.

## 3. Templates

**Work instruction** (fixed sections, same order every time):

1. Header: document number, revision, product and variant, station, status *Draft*.
2. Purpose (one line) and scope.
3. Kit list: BoM lines consumed here with quantities per unit.
4. Tools and equipment: machine class, fixtures, gauges.
5. Safety and ESD/Li-ion notes: from catalog hazard data; cannot be omitted.
6. Steps: numbered, each with the task it implements and its standard time (the sum equals the station's cycle).
7. Quality checks at this station: what, how often, limit, record, with a link to the inspection instruction.
8. What to do on failure (reaction plan): stop, tag, call, rework route.
9. Figure: a layout snapshot of the station from the 3D scene, when available.
10. Sign-off block (blank): prepared, reviewed, approved.

**Inspection instruction:** header · characteristic and limit · applies to (item, operation) · method · instrument · sample size and AQL with the table row used · frequency · accept/reject rule · record fields (check-sheet columns) · reaction plan · linked certification (e.g. BIS capacity verification) · sign-off.

Check sheets export as XLSX with the record columns pre-built.

## 4. How the documents are generated

ADR-001 holds: every number and every list of required content comes from engines or catalog data; the model supplies wording only.

1. **Assemble.** An engine builds a *document skeleton* per document: all fixed fields, tables and the list of steps the document must contain (one per routing task, one per consumed BoM line, one per quality check, one per hazard).
2. **Draft.** The `writer` role gets the skeleton and writes the step text and short descriptions as structured output (one object per required step). It cannot add or drop steps.
3. **Validate (coverage).** Per document:
   - every operation at the station has at least one step;
   - every BoM line consumed at the station appears in the kit list;
   - every quality check at the station appears in section 7;
   - every hazard from the catalog for the station's zone appears in section 5;
   - step times sum to the station load within 1 %;
   - no step mentions a machine, tool or number that is not in the skeleton.
   A document that fails gets up to two repair passes, then is shown with its failing checks.
4. **Review.** The user reviews documents in a list (status per document), edits text inline (edits are `source=user` and survive re-runs), and approves per document or in bulk.

SOPs are template text with parameters, so the model is only used to adapt sentences to the product.

## 5. Where it runs in the pipeline

Documents are produced in a **documents step inside S12 (reports)**, after simulation (S10) and finance (S11). Reasons: station, layout and quality data are final and approved; a change from the simulation or layout would otherwise make the documents stale and spend tokens twice; and one review at the end matches how the user will use them.

Consequences for earlier docs:

- S7 keeps work-order design, sample week and roster. Its "draft work instructions" item moves to S12 (doc 06 §2.1 updated).
- S4 must record, per check, the fields in §3 (characteristic, limit, method, instrument class, frequency, reaction). Where the catalog `check_type` lacks them, add them (additive catalog change, version bump, §8).
- A change set that touches an approved station or check makes only the affected documents stale. Document-level `inputs_hash` lets unchanged documents be reused.

### 5.1 Budget (estimate)

For one product with about 20 stations, about 15 checks and 7 SOPs: roughly 60k tokens in total, in batches of one document at a time so a run is resumable. This exceeds the usual per-stage budgets in doc 02 §4, so the documents step gets its own budget (80k, editable) and pauses with a resume estimate when quota runs low. Unchanged documents are not regenerated, so a re-run after a small change costs a fraction. These figures are planning estimates to be measured in P8b.

## 6. Formats and numbering

| Format | Used for | Tool |
|---|---|---|
| Web viewer with status, edit and approve | All documents | React |
| PDF, per document and as one combined handbook | WI, inspection instructions, SOPs, control plan, flow charts | WeasyPrint |
| XLSX | Check sheets, control plan, training matrix, equipment list | openpyxl |
| DOCX (editable) | WI and SOPs, so a shop team can adapt them | `python-docx` (proposed in ADR-023; licence and arm64 check at P8b) |

Numbering: `WI-<station>-<variant>-RevA`, `INS-<check>-RevA`, `SOP-<topic>-RevA`, `CP-<variant>-RevA`. Regenerated documents bump the revision only after approval.

ZIP layout inside the project export:

```
factory-package/
  01-build/    layout/, utilities/, equipment-list.xlsx, vendors.xlsx
  02-run/      work-instructions/, inspection/, control-plan/, flow-charts/, sops/, training-matrix.xlsx, work-orders/, roster.xlsx
  03-business/ financials.xlsx, compliance-checklist.pdf, assumptions.xlsx, simulation/
  00-index.pdf   (what each file is, versions of artifacts, catalog and pack)
```

## 7. Limits and disclaimers

- Drafts: written without seeing the real stations; times come from catalog estimates unless the user entered measured values.
- Not a quality-system certification: the documents help a first factory start; they are not an ISO 9001 or IATF audit pack.
- Compliance references are informational and dated (doc 10). Electrical, fire and structural content still needs licensed engineers.
- The platform does not execute any of this (no MES, no inspection data collection). It designs the documents and the work-order system.

## 8. Build plan (after P0 and P1)

| Phase | Work |
|---|---|
| P3 | S4 quality plan records the check fields in §3. Catalog `check_type` gets the missing fields (version `2026.11.2`; the P1 catalog is `2026.11.1`). |
| P5 | S7 drops draft work instructions; adds nothing else for documents. |
| P8 | `ReportPack` gains a `documents` list; S12 report sections and the BoM workbook (doc 03 §4.8). |
| **P8b** | The documents step: skeletons, templates, `writer` drafting, coverage validators, review UI, PDF/XLSX/DOCX export, ZIP layout, token measurement. Acceptance in doc 14. |

Tier 2 documents are not planned in the POC.
