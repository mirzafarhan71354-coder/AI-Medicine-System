import os
from pathlib import Path
import sqlite3

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    mobile TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    sms_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sms_enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    tablets_per_strip INTEGER NOT NULL CHECK (tablets_per_strip BETWEEN 1 AND 100),
    tablets_per_dose INTEGER NOT NULL CHECK (tablets_per_dose BETWEEN 1 AND 100),
    low_stock_threshold INTEGER NOT NULL CHECK (low_stock_threshold BETWEEN 0 AND 10000),
    alert_state TEXT NOT NULL DEFAULT 'ok'
        CHECK (alert_state IN ('ok', 'low', 'urgent', 'out')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, user_id)
);

CREATE TABLE IF NOT EXISTS medicine_tablets (
    id INTEGER PRIMARY KEY,
    medicine_id INTEGER NOT NULL REFERENCES medicines(id) ON DELETE CASCADE,
    tablet_number INTEGER NOT NULL,
    consumed_at TEXT,
    consumption_source TEXT,
    dose_id INTEGER,
    UNIQUE (medicine_id, tablet_number)
);

CREATE INDEX IF NOT EXISTS medicine_tablets_stock
    ON medicine_tablets (medicine_id, consumed_at, tablet_number);

CREATE TABLE IF NOT EXISTS medicine_schedules (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL,
    time_local TEXT NOT NULL,
    days_of_week TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medicine_id, user_id) REFERENCES medicines(id, user_id)
        ON DELETE CASCADE,
    UNIQUE (id, user_id)
);

CREATE TABLE IF NOT EXISTS dose_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL,
    schedule_id INTEGER NOT NULL,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'taken', 'skipped', 'snoozed')),
    snoozed_until TEXT,
    notification_round INTEGER NOT NULL DEFAULT 0,
    action_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medicine_id, user_id) REFERENCES medicines(id, user_id)
        ON DELETE CASCADE,
    FOREIGN KEY (schedule_id, user_id) REFERENCES medicine_schedules(id, user_id)
        ON DELETE CASCADE,
    UNIQUE (schedule_id, scheduled_for)
);

CREATE INDEX IF NOT EXISTS dose_events_user_time
    ON dose_events (user_id, scheduled_for, status);

CREATE TABLE IF NOT EXISTS inventory_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    medicine_id INTEGER NOT NULL,
    tablet_id INTEGER,
    dose_id INTEGER REFERENCES dose_events(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('dose_taken', 'manual_consumption', 'manual_correction')),
    quantity INTEGER NOT NULL CHECK (quantity != 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medicine_id, user_id) REFERENCES medicines(id, user_id)
        ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS inventory_events_dose_once
    ON inventory_events (dose_id) WHERE dose_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS notification_history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    medicine_id INTEGER,
    dose_id INTEGER REFERENCES dose_events(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (medicine_id, user_id) REFERENCES medicines(id, user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS notification_history_user_time
    ON notification_history (user_id, created_at);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY,
    notification_id INTEGER NOT NULL REFERENCES notification_history(id)
        ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('in_app', 'push', 'sms')),
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'failed')),
    provider_reference TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (notification_id, channel, attempt)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    subscription_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def database_path():
    configured_path = current_app.config.get("CAREPOINT_DATABASE")
    if configured_path:
        return Path(configured_path)
    return Path(__file__).resolve().parent / "data" / "carepoint.sqlite3"


def connect_database(path=None):
    database_file = Path(path) if path else Path(os.environ.get(
        "CAREPOINT_DATABASE",
        Path(__file__).resolve().parent / "data" / "carepoint.sqlite3",
    ))
    database_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_file, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


def get_database():
    if "reminder_database" not in g:
        g.reminder_database = connect_database(database_path())
    return g.reminder_database


def close_database(_error=None):
    connection = g.pop("reminder_database", None)
    if connection is not None:
        connection.close()


def init_database(app):
    configured_path = app.config.get("CAREPOINT_DATABASE") or os.environ.get(
        "CAREPOINT_DATABASE"
    )
    database_file = Path(configured_path) if configured_path else (
        Path(app.root_path) / "data" / "carepoint.sqlite3"
    )
    database_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_file, timeout=15)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    app.config["CAREPOINT_DATABASE"] = str(database_file)
    app.teardown_appcontext(close_database)
