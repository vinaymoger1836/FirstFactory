"""Deterministic text embedding for the catalog's pgvector index (ADR-020, Proposed).

Signed feature hashing of word unigrams, word bigrams and character trigrams into a fixed
256-dimension vector, L2-normalised. No model download, no network, identical on amd64 and
arm64, microseconds per entry, and it fits the 2 OCPU / 12 GB budget. It captures lexical
and sub-word similarity ("spot welding" ~ "spot_weld_nickel", "hipot" ~ "hi-pot"), not deep
semantics; aliases in the catalog carry the synonyms. Search combines it with Postgres
full-text ranking. A learned embedding can replace it later behind the same column
(re-embed on load; the dimension is stored with each version).
"""

from __future__ import annotations

import hashlib
import math
import re

__all__ = ["DIM", "EMBEDDING_MODEL", "embed", "to_pgvector"]

DIM = 256
EMBEDDING_MODEL = "hash-ngram-v1"
_TOKEN = re.compile(r"[a-z0-9]+")
_WEIGHTS = {"w": 1.0, "b": 0.6, "c": 0.35}


def _features(text: str) -> list[tuple[str, float]]:
    tokens = _TOKEN.findall(text.lower().replace("_", " "))
    feats: list[tuple[str, float]] = [(f"w:{t}", _WEIGHTS["w"]) for t in tokens]
    feats += [(f"b:{a}_{b}", _WEIGHTS["b"]) for a, b in zip(tokens, tokens[1:], strict=False)]
    for t in tokens:
        padded = f"#{t}#"
        feats += [(f"c:{padded[i:i + 3]}", _WEIGHTS["c"]) for i in range(len(padded) - 2)]
    return feats


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for feat, weight in _features(text):
        h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % DIM
        vec[idx] += weight if h[4] & 1 else -weight
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
