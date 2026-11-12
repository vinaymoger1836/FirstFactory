# Golden cases (docs/14 §4)

The contract every phase's end-to-end check runs against. Expected values change only with an
explanation in a phase report (CLAUDE.md rule 10).

| Case | Folder | What is fixed here | Asserted from |
|---|---|---|---|
| G1 Power bank | `power_bank/` | Wizard template, variant parameters, MBOM, routing in catalog codes, expected numbers | P4 (capacity); routing/catalog consistency from P1 |
| G2 BLDC fan | `ceiling_fan/` | Same as G1 | P4; routing/catalog consistency from P1 |
| G3 Utilities | `utilities/g3.yaml` | MD 180 kVA → transformer 250 kVA | P5 |
| G4 Finance | `finance/g4.yaml` | Cash flows [-100, 30×5] at 12% → NPV 8.14, IRR 15.24% | P8 |
| G5 Balancing | `benchmarks/salbp/` | Scholl (1993) SALBP-1 instances Jackson, Mertens, Bowman with optimal station counts | P4 |
| G6 Layout | `benchmarks/qap/g6.yaml` | 5-zone QAP with its brute-force optimum | P6 |
| G7 Simulation | `benchmarks/queueing/g7.yaml` | Deterministic serial line; M/M/1 at ρ = 0.8 | P7 |

Files per product case:

- `template.yaml`: the wizard input (`ProductTemplate` v1).
- `params.yaml`: structured per-variant product parameters (what S1 intake must produce; names follow the
  catalog archetype's parameter list). They equal the archetype's example variants in the catalog.
- `bom.yaml`: item master and MBOM lines (what S2 must produce, modulo IDs).
- `routing.yaml`: operations naming **catalog process codes only**, plus precedence. The P1 check builds the
  domain `Routing` from it, taking machine class, task type, quality gate and time standard from the catalog.
- `expected.yaml`: the exact assertions, and the catalog inputs they depend on.

Check them (no database needed):

```bash
uv run fl-library golden
```

`benchmarks/salbp/*.IN2` are copied unchanged from Scholl's SALBP benchmark data set (1993),
https://assembly-line-balancing.de/salbp/benchmark-data-sets-1993/ (file format: task count, task times,
precedence arcs `i,j`, terminated by `-1,-1`). Optima are from the data set's `SALBP data sets.xlsx`.
