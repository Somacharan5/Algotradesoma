"""
Apply db/schema.sql to Supabase via direct PostgreSQL connection.

Usage:
    python -m scripts.apply_schema

Requires SUPABASE_DB_URL in .env  (format below):
    postgresql://postgres.[project-ref]:[db-password]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres

Find your DB password in:
    Supabase Dashboard → Project Settings → Database → Connection string
"""
from __future__ import annotations

import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def main() -> None:
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        print(
            "\n[ERROR] SUPABASE_DB_URL not set.\n"
            "Add it to your .env:\n"
            "  SUPABASE_DB_URL=postgresql://postgres.[project-ref]:[password]"
            "@aws-0-ap-south-1.pooler.supabase.com:6543/postgres\n"
            "\nFind it in: Supabase Dashboard → Project Settings → Database → URI\n"
        )
        return

    sql = SCHEMA_PATH.read_text()
    print(f"Applying schema from {SCHEMA_PATH} …")

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(sql)
    cur.close()
    conn.close()

    print("Schema applied successfully.")


if __name__ == "__main__":
    main()
