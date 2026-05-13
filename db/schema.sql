-- ============================================================
-- Trading Agent — Supabase Schema
-- Run once in Supabase SQL editor or via migration
-- ============================================================

-- Enable UUID generation
create extension if not exists "pgcrypto";

-- ── signals ────────────────────────────────────────────────────────────────
-- Raw signals produced by the Strategy Engine before risk filtering
create table if not exists signals (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    symbol          text not null,           -- e.g. "RELIANCE"
    exchange        text not null default 'NSE',
    signal_type     text not null,           -- "BUY" | "SELL" | "EXIT"
    strategy        text not null,           -- strategy name/version
    price_at_signal numeric(12,2) not null,
    atr             numeric(10,4),
    metadata        jsonb default '{}'
);

-- ── trades ─────────────────────────────────────────────────────────────────
-- Every order sent to the broker (paper or live)
create table if not exists trades (
    id              uuid primary key default gen_random_uuid(),
    created_at      timestamptz not null default now(),
    signal_id       uuid references signals(id),
    mode            text not null default 'paper',   -- "paper" | "live"
    symbol          text not null,
    exchange        text not null default 'NSE',
    direction       text not null,           -- "BUY" | "SELL"
    quantity        integer not null,
    entry_price     numeric(12,2),
    exit_price      numeric(12,2),
    stop_loss       numeric(12,2),
    target          numeric(12,2),
    status          text not null default 'OPEN',    -- "OPEN" | "CLOSED" | "CANCELLED"
    broker_order_id text,
    pnl             numeric(12,2),
    closed_at       timestamptz,
    metadata        jsonb default '{}'
);

-- ── positions ──────────────────────────────────────────────────────────────
-- Current open positions (denormalised view for fast reads)
create table if not exists positions (
    id              uuid primary key default gen_random_uuid(),
    updated_at      timestamptz not null default now(),
    trade_id        uuid references trades(id),
    symbol          text not null unique,
    exchange        text not null default 'NSE',
    direction       text not null,
    quantity        integer not null,
    avg_price       numeric(12,2) not null,
    current_price   numeric(12,2),
    stop_loss       numeric(12,2),
    target          numeric(12,2),
    unrealised_pnl  numeric(12,2),
    mode            text not null default 'paper'
);

-- ── daily_pnl ──────────────────────────────────────────────────────────────
-- One row per trading day; used by circuit breaker and reporter
create table if not exists daily_pnl (
    id              uuid primary key default gen_random_uuid(),
    trade_date      date not null unique,
    realised_pnl    numeric(12,2) not null default 0,
    unrealised_pnl  numeric(12,2) not null default 0,
    num_trades      integer not null default 0,
    capital_start   numeric(14,2),
    capital_end     numeric(14,2),
    circuit_tripped boolean not null default false
);

-- ── audit_log ──────────────────────────────────────────────────────────────
-- Immutable append-only log for every agent decision
create table if not exists audit_log (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),
    layer       text not null,   -- "strategy" | "risk" | "compliance" | "execution" | "auditor" | "reporter"
    event       text not null,
    payload     jsonb default '{}',
    trade_id    uuid references trades(id)
);

-- ── indexes ────────────────────────────────────────────────────────────────
create index if not exists trades_symbol_status on trades(symbol, status);
create index if not exists trades_created_at    on trades(created_at desc);
create index if not exists audit_log_created_at on audit_log(created_at desc);
create index if not exists audit_log_layer      on audit_log(layer);
