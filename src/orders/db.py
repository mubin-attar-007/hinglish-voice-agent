"""SQLite schema and synthetic seed data for the illustrative order system."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, time, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    slot_id  TEXT PRIMARY KEY,
    day      TEXT NOT NULL,                       -- ISO date
    start    TEXT NOT NULL,                       -- HH:MM, local time (IST)
    end      TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    pincode  TEXT NOT NULL,
    item     TEXT NOT NULL,
    slot_id  TEXT NOT NULL REFERENCES slots (slot_id),
    version  INTEGER NOT NULL DEFAULT 1,
    other_customer INTEGER NOT NULL DEFAULT 0     -- 1 = filler order of another synthetic customer
);

CREATE TABLE IF NOT EXISTS idempotency (
    key      TEXT PRIMARY KEY,                    -- UNIQUE: one stored result per key
    order_id TEXT NOT NULL,
    slot_id  TEXT NOT NULL,
    result   TEXT NOT NULL                        -- JSON: status and commit sequence number
);

CREATE TABLE IF NOT EXISTS events (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    at_ms    INTEGER NOT NULL,                    -- virtual clock
    call_id  TEXT NOT NULL,
    order_id TEXT,
    kind     TEXT NOT NULL,
    data     TEXT NOT NULL DEFAULT '{}'           -- JSON
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'the event log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'the event log is append-only');
END;
"""

DEFAULT_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(10), time(12)),
    (time(12), time(14)),
    (time(14), time(16)),
    (time(16), time(18)),
    (time(18), time(20)),
)


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open a database with the schema applied. Transactions are explicit (autocommit mode)."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def slot_id_for(day: date, start: time) -> str:
    """Slot ids read as YYYYMMDD-HHMM, for example 20261128-1600."""
    return f"{day:%Y%m%d}-{start:%H%M}"


@dataclass(frozen=True)
class OrderSpec:
    order_id: str
    pincode: str
    item: str
    slot_id: str


@dataclass(frozen=True)
class WorldSpec:
    """The synthetic world one call runs in."""

    first_day: date
    days: int = 7
    windows: tuple[tuple[time, time], ...] = DEFAULT_WINDOWS
    capacity: int = 3
    orders: tuple[OrderSpec, ...] = ()
    full_slots: tuple[str, ...] = ()  # filled to capacity by other synthetic customers
    capacity_overrides: tuple[tuple[str, int], ...] = ()


def seed(conn: sqlite3.Connection, world: WorldSpec) -> None:
    overrides = dict(world.capacity_overrides)
    slot_rows = []
    for offset in range(world.days):
        day = world.first_day + timedelta(days=offset)
        for start, end in world.windows:
            slot_id = slot_id_for(day, start)
            capacity = overrides.get(slot_id, world.capacity)
            slot_rows.append((slot_id, day.isoformat(), f"{start:%H:%M}", f"{end:%H:%M}", capacity))
    conn.executemany(
        "INSERT INTO slots (slot_id, day, start, end, capacity) VALUES (?, ?, ?, ?, ?)", slot_rows
    )
    conn.executemany(
        "INSERT INTO orders (order_id, pincode, item, slot_id) VALUES (?, ?, ?, ?)",
        [(o.order_id, o.pincode, o.item, o.slot_id) for o in world.orders],
    )
    for slot_id in world.full_slots:
        fill_slot(conn, slot_id)


def fill_slot(conn: sqlite3.Connection, slot_id: str) -> int:
    """Fill a slot to capacity with other synthetic customers' orders. Returns how many."""
    row = conn.execute(
        "SELECT capacity, (SELECT COUNT(*) FROM orders WHERE slot_id = ?) AS booked "
        "FROM slots WHERE slot_id = ?",
        (slot_id, slot_id),
    ).fetchone()
    if row is None:
        return 0
    free = max(0, row["capacity"] - row["booked"])
    start = conn.execute("SELECT COUNT(*) FROM orders WHERE other_customer = 1").fetchone()[0]
    conn.executemany(
        "INSERT INTO orders (order_id, pincode, item, slot_id, other_customer) "
        "VALUES (?, '000000', 'another customer''s order', ?, 1)",
        [(f"other-{start + i + 1}", slot_id) for i in range(free)],
    )
    return free
