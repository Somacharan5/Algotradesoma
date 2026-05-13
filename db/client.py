from __future__ import annotations

from functools import lru_cache
from supabase import create_client, Client
from config import settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


async def insert(table: str, row: dict) -> dict:
    client = get_client()
    result = client.table(table).insert(row).execute()
    return result.data[0] if result.data else {}


async def upsert(table: str, row: dict, on_conflict: str = "id") -> dict:
    client = get_client()
    result = client.table(table).upsert(row, on_conflict=on_conflict).execute()
    return result.data[0] if result.data else {}


async def fetch(table: str, filters: dict | None = None, limit: int = 100) -> list[dict]:
    client = get_client()
    query = client.table(table).select("*").limit(limit)
    for col, val in (filters or {}).items():
        query = query.eq(col, val)
    result = query.execute()
    return result.data or []


async def log_audit(layer: str, event: str, payload: dict = {}, trade_id: str | None = None) -> None:
    row: dict = {"layer": layer, "event": event, "payload": payload}
    if trade_id:
        row["trade_id"] = trade_id
    await insert("audit_log", row)
