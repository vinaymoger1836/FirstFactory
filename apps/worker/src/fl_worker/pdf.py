"""PDF text extraction, run as a child process of the worker (docs/11 §2: PDFs parsed in
the worker, not the API; the worker has no internet egress).

``python -m fl_worker.pdf <input>`` prints ``{"pages": [{"page": n, "text": "..."}], ...}``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_PAGES = 30
MAX_CHARS_PER_PAGE = 8000


def main() -> int:
    import pdfplumber

    src = Path(sys.argv[1])
    pages: list[dict[str, object]] = []
    with pdfplumber.open(str(src)) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
            text = (page.extract_text() or "").strip()
            pages.append({"page": i, "text": text[:MAX_CHARS_PER_PAGE]})
    sys.stdout.write(
        json.dumps({"pages": pages, "page_count": total, "truncated": total > MAX_PAGES})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
