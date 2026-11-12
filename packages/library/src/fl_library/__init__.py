"""Library loader (docs/10): read the catalog and pack YAML, validate, load into Postgres.

- ``fl_library.io``      YAML → Pydantic models (schema errors become Issues)
- ``fl_library.checks``  catalog tests (docs/10 §1.4) and pack tests (§2.3)
- ``fl_library.embed``   deterministic text embedding for the pgvector index
- ``fl_library.db``      versioned load into Postgres and hybrid search
- ``fl_library.golden``  golden fixture checks (G1-G7 inputs, docs/14 §4)
- ``fl_library.cli``     ``fl-library validate | load | search | golden | pack-table``
"""

__all__: list[str] = []
