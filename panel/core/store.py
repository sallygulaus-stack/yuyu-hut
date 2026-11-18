"""Durable identity, outbox and receive checkpoints beside the legacy JSONL."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading


class ReliabilityStore:
    def __init__(self, root, history):
        self.path = Path(root) / "chat" / "reliability.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    endpoint TEXT, conversation TEXT, direction TEXT,
                    message TEXT, event TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(endpoint, conversation, direction, message));
                CREATE UNIQUE INDEX IF NOT EXISTS event_identity
                    ON messages(endpoint, conversation, direction, event) WHERE event != '';
                CREATE INDEX IF NOT EXISTS delivery_poll ON messages(
                    json_extract(payload,'$.endpoint_id'), direction,
                    json_extract(payload,'$.remote_endpoint_id'),
                    json_extract(payload,'$.delivery_status'));
                CREATE TABLE IF NOT EXISTS checkpoints (endpoint TEXT PRIMARY KEY, cursor INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS media (endpoint TEXT, remote TEXT, local TEXT,
                    PRIMARY KEY(endpoint, remote));
            """)
        # One recovery pass handles an interrupted append/index commit and legacy data.
        seen = set()
        for row in history() or ():
            if row.get("消息ID") or row.get("message_id"):
                value = self.from_record(row)
                seen.add((self.identity_endpoint(value), value["conversation_id"], value["direction"], value["message_id"]))
                self.put(value, replace=False)
        # Index is a recovery aid; deleted/rolled-back JSONL rows must never suppress a replay.
        with self.db() as db:
            for row in db.execute("SELECT endpoint,conversation,direction,message FROM messages").fetchall():
                if tuple(row) not in seen:
                    db.execute("DELETE FROM messages WHERE endpoint=? AND conversation=? AND direction=? AND message=?",
                               tuple(row))

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def from_record(row):
        result = dict(row)
        result.update(
            endpoint_id=str(row.get("endpoint_id") or "onebot-local"),
            conversation_id=str(row.get("conversation_id") or "main"),
            direction=str(row.get("direction") or ("out" if row.get("谁") == "我" else "in")),
            message_id=str(row.get("message_id") or row.get("消息ID") or ""),
            event_id=str(row.get("event_id") or row.get("事件ID") or ""),
            segments=row.get("segments") or row.get("段") or [],
            local_sequence=row.get("local_sequence", row.get("序号", row.get("序", 0))),
        )
        return result

    @classmethod
    def identity_endpoint(cls, value):
        endpoint = str(value.get("endpoint_id") or "onebot-local")
        return cls.endpoint_scope(endpoint, value["remote_endpoint_id"]) if value.get("remote_endpoint_id") else endpoint

    def find(self, endpoint, conversation, direction, message="", event="", remote_id=""):
        scope = self.endpoint_scope(endpoint, remote_id) if remote_id else endpoint
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM messages WHERE endpoint IN (?,?) AND conversation=? AND direction=? "
                "AND ((message=? AND ?!='') OR (event=? AND ?!='')) LIMIT 1",
                (scope, endpoint, conversation, direction, str(message), str(message), str(event), str(event)),
            ).fetchone()
            if row is None and not remote_id:
                # Retry by globally unique client identity, including a previous endpoint binding.
                row = db.execute(
                    "SELECT payload FROM messages WHERE json_extract(payload,'$.endpoint_id')=? "
                    "AND conversation=? AND direction=? AND message=? LIMIT 1",
                    (endpoint, conversation, direction, str(message))).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, message, replace=True):
        value = dict(message)
        with self.db() as db:
            db.execute(
                ("INSERT OR REPLACE" if replace else "INSERT OR IGNORE") +
                " INTO messages VALUES (?,?,?,?,?,?)",
                (self.identity_endpoint(value), value["conversation_id"],
                 value["direction"], value["message_id"], value.get("event_id", ""),
                 json.dumps(value, ensure_ascii=False)),
            )

    def cursor(self, endpoint):
        with self.db() as db:
            row = db.execute("SELECT cursor FROM checkpoints WHERE endpoint=?", (endpoint,)).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def endpoint_scope(endpoint, remote_id):
        return json.dumps([str(endpoint), str(remote_id)], separators=(",", ":"))

    def bind_endpoint(self, endpoint, remote_id, replace=False):
        """Migrate an old checkpoint once; a replacement always gets its own cursor."""
        key = "gateway:" + endpoint
        scope = self.endpoint_scope(endpoint, remote_id)
        with self.db() as db:
            old = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            previous = old[0] if old else ""
            if previous and previous != remote_id and not replace:
                return previous
            if previous == remote_id:
                legacy = db.execute("SELECT cursor FROM checkpoints WHERE endpoint=?", (endpoint,)).fetchone()
                if legacy:
                    db.execute("INSERT OR IGNORE INTO checkpoints VALUES (?,?)", (scope, legacy[0]))
            if previous != remote_id:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, remote_id))
                db.execute("INSERT OR IGNORE INTO checkpoints VALUES (?,0)", (scope,))
                if previous:
                    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                               ("rebound:" + scope, previous))
            return remote_id

    def outgoing(self, endpoint, statuses, limit=1, remote_id=None, checked_before=None):
        marks = ",".join("?" for _ in statuses)
        conditions = ["json_extract(payload,'$.endpoint_id')=?", "direction='out'",
                      "json_extract(payload,'$.delivery_status') IN (" + marks + ")"]
        values = [endpoint, *statuses]
        if remote_id is not None:
            conditions.append("json_extract(payload,'$.remote_endpoint_id')=?")
            values.append(remote_id)
        if checked_before is not None:
            conditions.append("COALESCE(json_extract(payload,'$.delivery_checked_at'),0)<=?")
            values.append(checked_before)
        with self.db() as db:
            rows = db.execute(
                "SELECT payload FROM messages WHERE " + " AND ".join(conditions) +
                " ORDER BY COALESCE(json_extract(payload,'$.delivery_checked_at'),0),rowid LIMIT ?",
                (*values, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def checkpoint(self, endpoint, cursor):
        with self.db() as db:
            db.execute("INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(endpoint) "
                       "DO UPDATE SET cursor=MAX(cursor,excluded.cursor)", (endpoint, int(cursor)))

    def setting(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))
                return str(value)
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def media_map(self, endpoint, remote, local=None):
        with self.db() as db:
            if local is not None:
                db.execute("INSERT OR REPLACE INTO media VALUES (?,?,?)", (endpoint, remote, local))
                return local
            row = db.execute("SELECT local FROM media WHERE endpoint=? AND remote=?",
                             (endpoint, remote)).fetchone()
        return row[0] if row else None

    def overlay(self, row):
        value = self.from_record(row)
        stored = self.find(value["endpoint_id"], value["conversation_id"], value["direction"],
                           value["message_id"], value["event_id"], value.get("remote_endpoint_id", ""))
        if stored:
            row = {**row, "delivery_status": stored.get("delivery_status", row.get("delivery_status")),
                   **{key: stored.get(key, "") for key in (
                       "delivery_error", "delivery_detail", "framework_status", "remote_endpoint_id")},
                   "gateway_accepted": stored.get("gateway_accepted", False)}
        return row
