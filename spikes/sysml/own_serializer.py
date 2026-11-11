"""Thin SysML v2 textual serializer for the subset in docs/04 §6 (spike version).

Deterministic: same input → byte-identical output. P8 moves the production version into
packages/engines/sysml and drives it from approved domain artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def name(n: str) -> str:
    """Basic names stay bare; anything else becomes an unrestricted name 'like this'."""
    if _IDENT.match(n):
        return n
    return "'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'"


@dataclass
class Attr:
    name: str
    type: str
    value: str | None = None


@dataclass
class PartDef:
    name: str
    specializes: str | None = None
    attrs: list[Attr] = field(default_factory=list)


@dataclass
class ActionDef:
    name: str


@dataclass
class Routing:
    """An action usage whose steps are sequenced with `then` (succession)."""

    name: str
    steps: list[tuple[str, str]]  # (usage name, action def)


@dataclass
class PartUsage:
    name: str
    type: str | None = None
    children: list[PartUsage] = field(default_factory=list)


@dataclass
class Allocate:
    source: str
    target: str


@dataclass
class RequirementDef:
    name: str
    attrs: list[Attr] = field(default_factory=list)


Member = PartDef | ActionDef | Routing | PartUsage | Allocate | RequirementDef


@dataclass
class Package:
    name: str
    imports: list[str]
    members: list[Member]


def _attrs(attrs: list[Attr], ind: str) -> list[str]:
    out = []
    for a in attrs:
        val = f" = {a.value}" if a.value is not None else ""
        out.append(f"{ind}attribute {name(a.name)} : {a.type}{val};")
    return out


def _body(lines: list[str], head: str) -> list[str]:
    return (
        [head + ";"]
        if not lines
        else [head + " {", *lines, head[: len(head) - len(head.lstrip())] + "}"]
    )


def _member(m: Member, ind: str) -> list[str]:
    nxt = ind + "    "
    match m:
        case PartDef():
            spec = f" :> {name(m.specializes)}" if m.specializes else ""
            return _body(_attrs(m.attrs, nxt), f"{ind}part def {name(m.name)}{spec}")
        case ActionDef():
            return [f"{ind}action def {name(m.name)};"]
        case Routing():
            lines = []
            for i, (u, t) in enumerate(m.steps):
                kw = "then action" if i else "action"
                lines.append(f"{nxt}{kw} {name(u)} : {name(t)};")
            return _body(lines, f"{ind}action {name(m.name)}")
        case PartUsage():
            typ = f" : {name(m.type)}" if m.type else ""
            kids = [ln for c in m.children for ln in _member(c, nxt)]
            return _body(kids, f"{ind}part {name(m.name)}{typ}")
        case Allocate():
            return [f"{ind}allocate {m.source} to {m.target};"]
        case RequirementDef():
            return _body(_attrs(m.attrs, nxt), f"{ind}requirement def {name(m.name)}")


def write(pkg: Package) -> str:
    ind = "    "
    lines = [f"package {name(pkg.name)} {{"]
    lines += [f"{ind}private import {i};" for i in pkg.imports]
    for m in pkg.members:
        lines += _member(m, ind)
    lines.append("}")
    return "\n".join(lines) + "\n"


def example() -> Package:
    """docs/04 §6 example fragment, as our domain → SysML mapping would emit it."""
    return Package(
        name="PowerBankFactory",
        # SI::* omitted: sysmlpy 0.98 bundled library has no SI package (P0 spike d).
        imports=["ISQ::*", "ScalarValues::*"],
        members=[
            PartDef("PowerBank", attrs=[Attr("ratedCapacity", "ElectricChargeValue")]),
            PartDef("PB10K", specializes="PowerBank"),
            ActionDef("SpotWeldPack"),
            ActionDef("FunctionalTest"),
            Routing("routingPB10K", [("weld", "SpotWeldPack"), ("test", "FunctionalTest")]),
            PartDef("Station"),
            PartUsage(
                "factory", children=[PartUsage("st04", "Station"), PartUsage("st07", "Station")]
            ),
            Allocate("routingPB10K.weld", "factory.st04"),
            RequirementDef("ThroughputReq", attrs=[Attr("unitsPerDay", "Real", "1000")]),
        ],
    )
