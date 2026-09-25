"""SQLite storage for portfolio lots, the watchlist, settings and cached AI reports."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    shares REAL NOT NULL,
    buy_price REAL NOT NULL,
    buy_date TEXT,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    target_price REAL,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_reports (
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    created TEXT NOT NULL,
    content TEXT NOT NULL,
    sources TEXT NOT NULL,
    PRIMARY KEY (symbol, kind)
);
"""

_lock = threading.Lock()
_path: Path = settings.db_path


def set_path(path: Path) -> None:
    """Point the app at another database file (used by tests)."""
    global _path
    _path = path
    init()


def current_path() -> Path:
    return _path


def init() -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(_SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        con = sqlite3.connect(_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def rows(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as con:
        cur = con.execute(sql, params)
        return cur.lastrowid


# --- positions --------------------------------------------------------------

def list_positions() -> list[dict]:
    return rows("SELECT * FROM positions ORDER BY symbol, buy_date")


def add_position(symbol: str, shares: float, buy_price: float, buy_date: str | None, note: str = "") -> int:
    return execute(
        "INSERT INTO positions (symbol, shares, buy_price, buy_date, note) VALUES (?, ?, ?, ?, ?)",
        (symbol.upper().strip(), shares, buy_price, buy_date, note),
    )


def delete_position(pid: int) -> None:
    execute("DELETE FROM positions WHERE id = ?", (pid,))


# --- watchlist --------------------------------------------------------------

def list_watchlist() -> list[dict]:
    return rows("SELECT * FROM watchlist ORDER BY symbol")


def upsert_watch(symbol: str, target_price: float | None, note: str = "") -> None:
    execute(
        "INSERT INTO watchlist (symbol, target_price, note) VALUES (?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET target_price = excluded.target_price, note = excluded.note",
        (symbol.upper().strip(), target_price, note),
    )


def delete_watch(symbol: str) -> None:
    execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))


# --- settings ---------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    r = rows("SELECT value FROM settings WHERE key = ?", (key,))
    return r[0]["value"] if r else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- AI report cache --------------------------------------------------------

def get_report(symbol: str, kind: str) -> dict | None:
    r = rows("SELECT * FROM ai_reports WHERE symbol = ? AND kind = ?", (symbol.upper(), kind))
    return r[0] if r else None


def save_report(symbol: str, kind: str, created: str, content: str, sources: str) -> None:
    execute(
        "INSERT INTO ai_reports (symbol, kind, created, content, sources) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, kind) DO UPDATE SET created = excluded.created, content = excluded.content, "
        "sources = excluded.sources",
        (symbol.upper(), kind, created, content, sources),
    )
