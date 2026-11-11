"""Spike d: write the docs/04 §6 example with our serializer and with sysml2kit; parse both with
sysmlpy (syntax) and run sysmlpy.analyze (semantics).

    uv run --no-project --exclude-newer 2026-10-28 --python 3.12 \
        --with sysml2kit==0.4.0 --with sysmlpy==0.98.0 python spikes/sysml/run.py
"""

import json
import sys
import time
from importlib.metadata import version
from pathlib import Path

import sysmlpy

sys.path.insert(0, str(Path(__file__).parent))
import own_serializer

OUT = Path(__file__).parent / "out"


def check(label: str, text: str) -> dict:
    res: dict = {"chars": len(text)}
    (OUT / f"{label}.sysml").write_text(text, encoding="utf-8")
    t0 = time.perf_counter()
    try:
        model = sysmlpy.loads(text)
        res["parse"] = "ok"
    except Exception as exc:
        res["parse"] = f"FAIL {type(exc).__name__}: {str(exc)[:300]}"
        return res
    res["parse_ms"] = round((time.perf_counter() - t0) * 1000)
    try:
        analysis = sysmlpy.analyze(model, style_checks=False)
        # AnalysisResult is a list of SemanticIssue with .errors / .warnings views.
        issues = list(analysis)
        res["analyze_errors"] = len(analysis.errors)
        res["analyze_warnings"] = len(analysis.warnings)
        res["issues"] = [
            f"{getattr(i, 'severity', '')} {getattr(i, 'code', '')}: {getattr(i, 'message', i)}"[
                :200
            ]
            for i in issues[:10]
        ]
    except Exception as exc:
        res["analyze"] = f"FAIL {type(exc).__name__}: {str(exc)[:300]}"
    return res


def with_sysml2kit() -> tuple[str, list[str]]:
    from sysml2kit import Model
    from sysml2kit import builder as b
    from sysml2kit.text.writer import write_model

    gaps: list[str] = []
    m = Model()
    pkg = b.pkg(m, "PowerBankFactory")
    pkg.imports.extend(["ISQ::*", "ScalarValues::*"])
    pb = b.part_def(m, "PowerBank", owner=pkg)
    b.attr(m, "ratedCapacity", owner=pb, unit=None)
    pb10 = b.part_def(m, "PB10K", owner=pkg)
    spec_fields = [
        f for f in type(pb10).model_fields if "special" in f.lower() or "super" in f.lower()
    ]
    if spec_fields:
        setattr(pb10, spec_fields[0], [b.Ref(target=pb.id)] if "s" in spec_fields[0][-1] else pb.id)
    else:
        gaps.append("no specialization (:>) on part def")
    gaps.append("no action def / action usage / succession (routing) in builder")
    station = b.part_def(m, "Station", owner=pkg)
    factory = b.part(m, "factory", owner=pkg)
    st04 = b.part(m, "st04", owner=factory, definition=station)
    b.part(m, "st07", owner=factory, definition=station)
    gaps.append("allocate needs an action usage as source; allocated factory part instead")
    b.allocate(m, source=factory, target=st04, owner=pkg)
    rd = b.req_def(m, "ThroughputReq", owner=pkg)
    b.attr(m, "unitsPerDay", 1000, owner=rd)
    return write_model(m) if callable(write_model) else "", gaps


def main() -> None:
    OUT.mkdir(exist_ok=True)
    result: dict = {
        "versions": {p: version(p) for p in ("sysmlpy", "sysml2kit", "antlr4-python3-runtime")},
    }
    own = own_serializer.write(own_serializer.example())
    result["own"] = check("own", own)
    result["own"]["deterministic"] = own == own_serializer.write(own_serializer.example())

    try:
        kit_text, gaps = with_sysml2kit()
        if isinstance(kit_text, dict):  # write_model may return {package: text}
            kit_text = "\n".join(kit_text.values())
        result["sysml2kit"] = check("sysml2kit", str(kit_text))
        result["sysml2kit"]["gaps"] = gaps
    except Exception as exc:
        result["sysml2kit"] = {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}

    # Negative control: sysmlpy must reject broken syntax.
    result["negative_control"] = check("broken", "package P { part def A { attribute x : ; }")

    # Semantic negative control: analyze must flag unresolved types.
    result["negative_control_semantic"] = check(
        "unresolved", "package P { part def A { attribute x : NoSuchType; } part a : MissingDef; }"
    )

    print(json.dumps(result, indent=2))
    (OUT / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
