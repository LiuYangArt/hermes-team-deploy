"""Durable, serial work ownership for this job only."""
import json
import sqlite3
import time
from contextlib import contextmanager


class QueueStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL)""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        value = dict(row)
        for key in ("payload", "result"):
            if value[key] is not None:
                value[key] = json.loads(value[key])
        return value

    def enqueue(self, key, payload):
        now = time.time()
        with self.connection() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO events(key,payload,created,updated) VALUES(?,?,?,?)",
                (key, json.dumps(payload), now, now))
            return cursor.rowcount == 1

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM events WHERE status='pending' ORDER BY created,key LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE events SET status='processing',attempts=attempts+1,updated=? WHERE key=?",
                (time.time(), row["key"]))
            return self.decode(db.execute(
                "SELECT * FROM events WHERE key=?", (row["key"],)).fetchone())

    def finish(self, key, result):
        with self.connection() as db:
            db.execute("UPDATE events SET status='done',result=?,error=NULL,updated=? WHERE key=?",
                       (json.dumps(result), time.time(), key))

    def checkpoint(self, key, result):
        with self.connection() as db:
            db.execute("UPDATE events SET result=?,updated=? WHERE key=?",
                       (json.dumps(result), time.time(), key))

    def fail(self, key, error):
        with self.connection() as db:
            db.execute("UPDATE events SET status='failed',error=?,updated=? WHERE key=?",
                       (str(error)[:2000], time.time(), key))

    def retry(self, key):
        with self.connection() as db:
            return db.execute(
                "UPDATE events SET status='pending',updated=? WHERE key=? AND status='failed'",
                (time.time(), key)).rowcount == 1

    def recover(self):
        with self.connection() as db:
            return db.execute(
                "UPDATE events SET status='pending',updated=? WHERE status='processing'",
                (time.time(),)).rowcount

    def get(self, key):
        with self.connection() as db:
            return self.decode(db.execute(
                "SELECT * FROM events WHERE key=?", (key,)).fetchone())

    def retry_transient(self):
        with self.connection() as db:
            return db.execute("""UPDATE events SET status='pending',updated=?
                WHERE status='failed' AND attempts<3 AND updated<?""",
                (time.time(), time.time() - 60)).rowcount
