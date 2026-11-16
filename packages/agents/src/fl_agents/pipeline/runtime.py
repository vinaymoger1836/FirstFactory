"""Process-wide services for graph nodes (graph state holds IDs only, ADR-003)."""

from __future__ import annotations

from dataclasses import dataclass

from fl_agents.llm.router import Router
from fl_agents.settings import Settings, get_settings
from fl_agents.store.repo import Store, make_engine

__all__ = ["Runtime", "runtime", "set_runtime"]


@dataclass
class Runtime:
    settings: Settings
    store: Store
    router: Router


_RT: Runtime | None = None


def set_runtime(rt: Runtime) -> None:
    global _RT
    _RT = rt


def runtime() -> Runtime:
    global _RT
    if _RT is None:
        s = get_settings()
        store = Store(make_engine(s.database_url, "fl-agents"))
        _RT = Runtime(settings=s, store=store, router=Router(s, store))
    return _RT
