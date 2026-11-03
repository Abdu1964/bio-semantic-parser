"""Shared helpers for the local copies of lookup sources: one folder and SQLite file per source."""
import json
import os
import sqlite3
import threading

import requests

ROOT = os.getenv("LOCAL_SOURCES_DIR", "data/local_sources")
_lock = threading.Lock()
_conns: dict = {}


def path(*parts) -> str:
    return os.path.join(ROOT, *parts)


def norm(text: str) -> str:
    """Matching key: lower case, single spaces."""
    return " ".join((text or "").lower().split())


def connect(db_path: str):
    """Read-only connection to a built copy, or None while it is not built."""
    with _lock:
        if db_path not in _conns and os.path.exists(db_path):
            _conns[db_path] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        return _conns.get(db_path)


def query(db_path: str, sql: str, args=()) -> list:
    conn = connect(db_path)
    if conn is None:
        return []
    with _lock:
        return conn.execute(sql, args).fetchall()


def download(url: str, dest: str) -> str:
    """Stream a file to disk; a partial download never replaces a complete one."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with requests.get(url, stream=True, timeout=(30, 1800)) as r:
        r.raise_for_status()
        with open(dest + ".part", "wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
    os.replace(dest + ".part", dest)
    return dest


def new_db(db_path: str, schema: str) -> sqlite3.Connection:
    """A fresh build file next to the copy; swap_in() replaces the copy only once it is complete."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = db_path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.executescript(schema + "\nCREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
    return conn


def swap_in(conn: sqlite3.Connection, db_path: str, meta: dict) -> None:
    conn.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    conn.commit()
    conn.close()
    with _lock:
        old = _conns.pop(db_path, None)
        if old is not None:
            old.close()
        os.replace(db_path + ".building", db_path)


def meta(db_path: str, key: str, default=None):
    rows = query(db_path, "SELECT value FROM meta WHERE key=?", (key,))
    return json.loads(rows[0][0]) if rows else default
