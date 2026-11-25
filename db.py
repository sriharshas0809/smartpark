"""SQLite storage for users, parking history and notifications."""
import os
import sqlite3
from datetime import datetime
from flask import g
from werkzeug.security import generate_password_hash

DB_PATH = os.environ.get("SMARTPARK_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "smartpark.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, lot_id INTEGER, lot_name TEXT,
  ticket_id TEXT, plate TEXT, kind TEXT, slot_id TEXT, entry_time TEXT, exit_time TEXT,
  minutes REAL, multiplier REAL, fee REAL, method TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, message TEXT NOT NULL,
  created TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, args=(), one=False):
    rows = get_db().execute(sql, args).fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def ex(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify(user_id, message):
    ex("INSERT INTO notifications(user_id,message,created) VALUES(?,?,?)", (user_id, message, now_str()))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        pw = os.environ.get("SMARTPARK_ADMIN_PASSWORD", "admin123")
        conn.execute("INSERT INTO users(username,email,password_hash,role,created) VALUES(?,?,?,?,?)",
                     ("admin", "admin@smartpark.local", generate_password_hash(pw), "admin", now_str()))
    conn.commit()
    conn.close()
