"""
Durable state database — SQLite WAL, append-only events, typed order lifecycle.

Replaces JSON-on-every-mutation pattern. Single source of truth for:
- positions (current state)
- orders (entry/exit orders)
- algo_orders (SL/TP trailing orders via Algo Service)
- events (immutable audit log)

Research-backed schema from Deep Research #8.
"""

import sqlite3
import time
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from dataclasses import dataclass, field


DB_PATH = Path("data/state.sqlite")


def get_db() -> sqlite3.Connection:
    """Get a connection to the state database. Creates tables if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            side TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ENTRY_FILLED',
            entry_price REAL NOT NULL,
            size REAL NOT NULL,
            stop_loss REAL,
            take_profit REAL,
            leverage INTEGER DEFAULT 3,
            signal_score REAL,
            entry_time REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            client_order_id TEXT PRIMARY KEY,
            exchange_order_id TEXT,
            symbol TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('ENTRY','SL','TP','TRAIL','EXIT')),
            status TEXT NOT NULL DEFAULT 'LOCAL_INTENT',
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            price REAL,
            qty REAL NOT NULL,
            filled_qty REAL DEFAULT 0,
            error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS algo_orders (
            client_algo_id TEXT PRIMARY KEY,
            algo_id TEXT,
            symbol TEXT NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('SL','TP','TRAIL')),
            algo_status TEXT NOT NULL DEFAULT 'LOCAL_INTENT',
            order_type TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            close_position INTEGER DEFAULT 1,
            callback_rate REAL,
            actual_order_id TEXT,
            reject_reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol, ts);
        CREATE INDEX IF NOT EXISTS idx_algo_orders_symbol ON algo_orders(symbol, algo_status);
        CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol, status);
    """)


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Context manager for atomic transactions."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ═══════════════════════════════════════════
# POSITIONS
# ═══════════════════════════════════════════

def upsert_position(conn: sqlite3.Connection, data: Dict[str, Any]):
    now = time.time()
    data["updated_at"] = now
    with transaction(conn):
        conn.execute("""
            INSERT OR REPLACE INTO positions
            (symbol, side, state, entry_price, size, stop_loss, take_profit,
             leverage, signal_score, entry_time, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["symbol"],
            data.get("side", "long"),
            data.get("state", "ENTRY_FILLED"),
            data.get("entry_price", 0.0),
            data.get("size", 0.0),
            data.get("stop_loss"),
            data.get("take_profit"),
            data.get("leverage", 3),
            data.get("signal_score"),
            data.get("entry_time", now),
            now,
        ))


def delete_position(conn: sqlite3.Connection, symbol: str):
    with transaction(conn):
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))


def get_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM positions").fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════

def insert_order(conn: sqlite3.Connection, data: Dict[str, Any]):
    now = time.time()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    with transaction(conn):
        conn.execute("""
            INSERT INTO orders
            (client_order_id, exchange_order_id, symbol, purpose, status,
             side, order_type, price, qty, filled_qty, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["client_order_id"],
            data.get("exchange_order_id"),
            data["symbol"],
            data["purpose"],
            data.get("status", "LOCAL_INTENT"),
            data.get("side", ""),
            data.get("order_type", ""),
            data.get("price"),
            data.get("qty", 0.0),
            data.get("filled_qty", 0.0),
            data.get("error"),
            data["created_at"],
            data["updated_at"],
        ))


def update_order_status(conn: sqlite3.Connection, client_order_id: str,
                        status: str, exchange_order_id: Optional[str] = None,
                        error: Optional[str] = None):
    now = time.time()
    with transaction(conn):
        if exchange_order_id:
            conn.execute("""
                UPDATE orders SET status=?, exchange_order_id=?, updated_at=?
                WHERE client_order_id=?
            """, (status, exchange_order_id, now, client_order_id))
        else:
            conn.execute("""
                UPDATE orders SET status=?, updated_at=?, error=?
                WHERE client_order_id=?
            """, (status, now, error, client_order_id))


def get_order(conn: sqlite3.Connection, client_order_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)
    ).fetchone()
    return dict(row) if row else None


# ═══════════════════════════════════════════
# ALGO ORDERS
# ═══════════════════════════════════════════

def insert_algo_order(conn: sqlite3.Connection, data: Dict[str, Any]):
    now = time.time()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)
    with transaction(conn):
        conn.execute("""
            INSERT INTO algo_orders
            (client_algo_id, algo_id, symbol, purpose, algo_status, order_type,
             trigger_price, close_position, callback_rate, actual_order_id,
             reject_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["client_algo_id"],
            data.get("algo_id"),
            data["symbol"],
            data["purpose"],
            data.get("algo_status", "LOCAL_INTENT"),
            data.get("order_type", ""),
            data.get("trigger_price", 0.0),
            1 if data.get("close_position", True) else 0,
            data.get("callback_rate"),
            data.get("actual_order_id"),
            data.get("reject_reason"),
            data["created_at"],
            data["updated_at"],
        ))


def update_algo_status(conn: sqlite3.Connection, client_algo_id: str,
                       algo_status: str, algo_id: Optional[str] = None,
                       actual_order_id: Optional[str] = None,
                       reject_reason: Optional[str] = None):
    now = time.time()
    with transaction(conn):
        conn.execute("""
            UPDATE algo_orders
            SET algo_status=?, algo_id=COALESCE(?, algo_id),
                actual_order_id=COALESCE(?, actual_order_id),
                reject_reason=COALESCE(?, reject_reason),
                updated_at=?
            WHERE client_algo_id=?
        """, (algo_status, algo_id, actual_order_id, reject_reason, now, client_algo_id))


def get_open_algo_orders(conn: sqlite3.Connection,
                          symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    if symbol:
        rows = conn.execute(
            """SELECT * FROM algo_orders
               WHERE algo_status IN ('LOCAL_INTENT','SUBMITTED','NEW')
               AND symbol=?""", (symbol,)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM algo_orders
               WHERE algo_status IN ('LOCAL_INTENT','SUBMITTED','NEW')"""
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# EVENTS (immutable audit log)
# ═══════════════════════════════════════════

def log_event(conn: sqlite3.Connection, event_type: str, symbol: str, payload: Any):
    now = time.time()
    conn.execute("""
        INSERT INTO events (ts, type, symbol, payload_json)
        VALUES (?, ?, ?, ?)
    """, (now, event_type, symbol, json.dumps(payload, default=str)))


def get_recent_events(conn: sqlite3.Connection, symbol: Optional[str] = None,
                      limit: int = 50) -> List[Dict[str, Any]]:
    if symbol:
        rows = conn.execute(
            "SELECT * FROM events WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
