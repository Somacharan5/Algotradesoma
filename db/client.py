from __future__ import annotations

from functools import lru_cache
from supabase import create_client, Client
from config import settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


# ── Sync API (used by Executor, Auditor, Reporter) ─────────────────────────

def db_insert(table: str, row: dict) -> dict:
    result = get_client().table(table).insert(row).execute()
    return result.data[0] if result.data else {}


def db_update(table: str, match: dict, updates: dict) -> dict:
    q = get_client().table(table).update(updates)
    for col, val in match.items():
        q = q.eq(col, val)
    result = q.execute()
    return result.data[0] if result.data else {}


def db_upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    result = get_client().table(table).upsert(row, on_conflict=on_conflict).execute()
    return result.data[0] if result.data else {}


def db_fetch(table: str, filters: dict | None = None, limit: int = 100) -> list[dict]:
    q = get_client().table(table).select("*").limit(limit)
    for col, val in (filters or {}).items():
        q = q.eq(col, val)
    return q.execute().data or []


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
