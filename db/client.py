from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import httpx
from loguru import logger
from supabase import create_client, Client

from config import settings

# ── Backend selection ───────────────────────────────────────────────────────
# Primary backend is Supabase. If it is unreachable (project paused/deleted,
# DNS failure, no network) we fall back to a local SQLite mirror of the same
# schema so the agent keeps running. Supabase is retried on next process start.

_CONN_ERRORS = (httpx.TransportError, OSError)

_SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "local_fallback.db"

_use_sqlite = not (settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)
_sqlite_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


def _activate_fallback(exc: Exception) -> None:
    global _use_sqlite
    if not _use_sqlite:
        _use_sqlite = True
        logger.warning(
            f"Supabase unreachable ({exc.__class__.__name__}: {exc}) — "
            f"falling back to local SQLite at {_SQLITE_PATH}"
        )


# ── SQLite fallback ─────────────────────────────────────────────────────────

_SQLITE_DDL = """
create table if not exists signals (
    id              text primary key,
    created_at      text,
    symbol          text not null,
    exchange        text not null default 'NSE',
    signal_type     text not null,
    strategy        text not null,
    price_at_signal real not null,
    atr             real,
    metadata        text default '{}'
);
create table if not exists trades (
    id              text primary key,
    created_at      text,
    signal_id       text,
    mode            text not null default 'paper',
    symbol          text not null,
    exchange        text not null default 'NSE',
    direction       text not null,
    quantity        integer not null,
    entry_price     real,
    exit_price      real,
    stop_loss       real,
    target          real,
    status          text not null default 'OPEN',
    broker_order_id text,
    pnl             real,
    closed_at       text,
    metadata        text default '{}'
);
create table if not exists positions (
    id              text primary key,
    updated_at      text,
    trade_id        text,
    symbol          text not null unique,
    exchange        text not null default 'NSE',
    direction       text not null,
    quantity        integer not null,
    avg_price       real not null,
    current_price   real,
    stop_loss       real,
    target          real,
    unrealised_pnl  real,
    mode            text not null default 'paper'
);
create table if not exists daily_pnl (
    id              text primary key,
    trade_date      text not null unique,
    realised_pnl    real not null default 0,
    unrealised_pnl  real not null default 0,
    num_trades      integer not null default 0,
    capital_start   real,
    capital_end     real,
    circuit_tripped integer not null default 0
);
create table if not exists audit_log (
    id          text primary key,
    created_at  text,
    layer       text not null,
    event       text not null,
    payload     text default '{}',
    trade_id    text
);
"""

_TIMESTAMP_COLS = {
    "signals": "created_at",
    "trades": "created_at",
    "positions": "updated_at",
    "audit_log": "created_at",
}
_JSON_COLS = {"metadata", "payload"}


@lru_cache(maxsize=1)
def _sqlite_conn() -> sqlite3.Connection:
    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SQLITE_DDL)
    conn.commit()
    return conn


def _encode(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        elif isinstance(v, bool):
            v = int(v)
        out[k] = v
    return out


def _decode(raw: sqlite3.Row) -> dict:
    row = dict(raw)
    for col in _JSON_COLS:
        if isinstance(row.get(col), str):
            try:
                row[col] = json.loads(row[col])
            except ValueError:
                pass
    return row


def _with_defaults(table: str, row: dict) -> dict:
    row = dict(row)
    row.setdefault("id", str(uuid.uuid4()))
    ts_col = _TIMESTAMP_COLS.get(table)
    if ts_col:
        row.setdefault(ts_col, datetime.now(timezone.utc).isoformat())
    return row


def _sq_insert(table: str, row: dict) -> dict:
    row = _encode(_with_defaults(table, row))
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _sqlite_lock:
        conn = _sqlite_conn()
        conn.execute(f"insert into {table} ({cols}) values ({marks})", list(row.values()))
        conn.commit()
        got = conn.execute(f"select * from {table} where id = ?", (row["id"],)).fetchone()
    return _decode(got) if got else {}


def _sq_update(table: str, match: dict, updates: dict) -> dict:
    updates = _encode(updates)
    match = _encode(match)
    set_sql = ", ".join(f"{c} = ?" for c in updates)
    where_sql = " and ".join(f"{c} = ?" for c in match)
    with _sqlite_lock:
        conn = _sqlite_conn()
        conn.execute(
            f"update {table} set {set_sql} where {where_sql}",
            list(updates.values()) + list(match.values()),
        )
        conn.commit()
        got = conn.execute(f"select * from {table} where {where_sql} limit 1", list(match.values())).fetchone()
    return _decode(got) if got else {}


def _sq_upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    row = _encode(_with_defaults(table, row))
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    set_sql = ", ".join(f"{c} = excluded.{c}" for c in row if c != on_conflict)
    with _sqlite_lock:
        conn = _sqlite_conn()
        conn.execute(
            f"insert into {table} ({cols}) values ({marks}) "
            f"on conflict({on_conflict}) do update set {set_sql}",
            list(row.values()),
        )
        conn.commit()
        got = conn.execute(
            f"select * from {table} where {on_conflict} = ?", (row[on_conflict],)
        ).fetchone()
    return _decode(got) if got else {}


def _sq_fetch(table: str, filters: dict | None = None, limit: int = 100) -> list[dict]:
    filters = _encode(filters or {})
    where_sql = (" where " + " and ".join(f"{c} = ?" for c in filters)) if filters else ""
    with _sqlite_lock:
        conn = _sqlite_conn()
        rows = conn.execute(
            f"select * from {table}{where_sql} limit ?", list(filters.values()) + [limit]
        ).fetchall()
    return [_decode(r) for r in rows]


# ── Sync API (used by Executor, Auditor, Reporter) ─────────────────────────

def db_insert(table: str, row: dict) -> dict:
    if not _use_sqlite:
        try:
            result = get_client().table(table).insert(row).execute()
            return result.data[0] if result.data else {}
        except _CONN_ERRORS as exc:
            _activate_fallback(exc)
    return _sq_insert(table, row)


def db_update(table: str, match: dict, updates: dict) -> dict:
    if not _use_sqlite:
        try:
            q = get_client().table(table).update(updates)
            for col, val in match.items():
                q = q.eq(col, val)
            result = q.execute()
            return result.data[0] if result.data else {}
        except _CONN_ERRORS as exc:
            _activate_fallback(exc)
    return _sq_update(table, match, updates)


def db_upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    if not _use_sqlite:
        try:
            result = get_client().table(table).upsert(row, on_conflict=on_conflict).execute()
            return result.data[0] if result.data else {}
        except _CONN_ERRORS as exc:
            _activate_fallback(exc)
    return _sq_upsert(table, row, on_conflict)


def db_fetch(table: str, filters: dict | None = None, limit: int = 100) -> list[dict]:
    if not _use_sqlite:
        try:
            q = get_client().table(table).select("*").limit(limit)
            for col, val in (filters or {}).items():
                q = q.eq(col, val)
            return q.execute().data or []
        except _CONN_ERRORS as exc:
            _activate_fallback(exc)
    return _sq_fetch(table, filters, limit)


def db_audit(layer: str, event: str, payload: dict | None = None, trade_id: str | None = None) -> None:
    row: dict = {"layer": layer, "event": event, "payload": payload or {}}
    if trade_id:
        row["trade_id"] = trade_id
    db_insert("audit_log", row)


# ── Async wrappers (used by Day 9 async orchestrator) ─────────────────────

import asyncio


async def insert(table: str, row: dict) -> dict:
    return await asyncio.to_thread(db_insert, table, row)


async def update(table: str, match: dict, updates: dict) -> dict:
    return await asyncio.to_thread(db_update, table, match, updates)


async def upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    return await asyncio.to_thread(db_upsert, table, row, on_conflict)


async def fetch(table: str, filters: dict | None = None, limit: int = 100) -> list[dict]:
    return await asyncio.to_thread(db_fetch, table, filters, limit)


async def log_audit(layer: str, event: str, payload: dict | None = None, trade_id: str | None = None) -> None:
    await asyncio.to_thread(db_audit, layer, event, payload, trade_id)
