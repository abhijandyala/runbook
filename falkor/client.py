"""Shared FalkorDB connection for runbook's operational-memory graph."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from falkordb import FalkorDB
from falkordb.graph import Graph

GRAPH_NAME = "runbook"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_database() -> FalkorDB:
    load_dotenv()
    host = os.getenv("FALKOR_HOST")
    port = os.getenv("FALKOR_PORT")
    password = os.getenv("FALKOR_PASS")
    if not host or not port or not password:
        raise ValueError("FALKOR_HOST, FALKOR_PORT, and FALKOR_PASS are required")

    return FalkorDB(
        host=host,
        port=int(port),
        username=os.getenv("FALKOR_USER", "falkordb"),
        password=password,
        ssl=_env_bool("FALKOR_SSL", default=False),
        socket_connect_timeout=10,
        socket_timeout=15,
    )


def get_graph() -> Graph:
    return get_database().select_graph(GRAPH_NAME)


def ping() -> bool:
    from falkor.queries import PING_QUERY

    result = get_graph().query(PING_QUERY)
    return bool(result.result_set and result.result_set[0][0] == 1)
