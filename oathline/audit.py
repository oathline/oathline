"""Append-only, hash-chained audit log.

Every event is linked to the previous one by SHA-256, so editing or deleting
an event, or inserting one in the middle, is detectable by `verify()`.
The SQLite table refuses UPDATE and DELETE with triggers, and appends run
under BEGIN IMMEDIATE so sequence numbers and links are computed under the
write lock. The triggers stop accidents: someone with the file or the
connection can remove them, or use INSERT OR REPLACE. They are created with
the table, once; opening an existing log writes nothing and takes no lock,
so a log can be read and verified while another process is writing to it.

Honest limit: someone who can write to the file can cut events off the end,
or rewrite events from any point to the end and recompute their hashes.
`verify()` on its own then passes. Keep a copy of `head()` somewhere else (a
ticket, an email, another system) and pass it to `verify()` to catch that.

Canonical form: JSON with sorted keys, compact separators, UTF-8.
  payload_hash = sha256(canonical(payload))
  event_hash   = sha256(canonical(body))  where body holds every field below
  genesis      = sha256("OATHLINE-AUDIT-GENESIS:" + chain_id)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Callable

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 16384
GENESIS_PREFIX = "OATHLINE-AUDIT-GENESIS:"
BODY_FIELDS = ("chain_id", "seq", "ts", "event_type", "principal", "action",
               "payload_hash", "prev_hash", "schema_version")


class AuditError(Exception):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def genesis(chain_id: str) -> str:
    return sha((GENESIS_PREFIX + chain_id).encode("utf-8"))


class AuditLog:
    def __init__(self, path: str = ":memory:", *, chain_id: str = "oathline-audit-001",
                 clock_us: Callable[[], int] = lambda: time.time_ns() // 1000):
        if not chain_id or len(chain_id) > 64:
            raise AuditError("chain_id must be a non-empty str <= 64")
        self.chain_id = chain_id
        self._clock_us = clock_us
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10)
        try:
            # Opening an existing log writes nothing, so it needs no lock and cannot wait on a writer.
            # The table and its triggers are created together, once, when the file is new.
            exists = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit'").fetchone()
            if not exists:
                self._db.executescript(
                    "CREATE TABLE IF NOT EXISTS audit("
                    " seq INTEGER PRIMARY KEY, chain_id TEXT NOT NULL, ts INTEGER NOT NULL,"
                    " event_type TEXT NOT NULL, principal TEXT NOT NULL, action TEXT NOT NULL,"
                    " payload TEXT NOT NULL, payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL,"
                    " event_hash TEXT NOT NULL UNIQUE, schema_version INTEGER NOT NULL);"
                    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit"
                    " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;"
                    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit"
                    " BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;")
        except BaseException:                        # a failed open leaves no connection behind
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- writing
    def append(self, event_type: str, principal: str, action: str, payload: dict | None = None) -> dict:
        payload = {} if payload is None else payload
        for name, val, lim in (("event_type", event_type, 64), ("principal", principal, 128),
                               ("action", action, 128)):
            if type(val) is not str or not val or len(val) > lim:
                raise AuditError(f"{name} must be a non-empty str <= {lim}")
        if not isinstance(payload, dict):
            raise AuditError("payload must be a dict")
        try:
            # store the payload as JSON reads it back (number keys become text), so verify() sees the same bytes
            payload_b = canonical(json.loads(canonical(payload)))
        except Exception as e:  # noqa: BLE001
            raise AuditError(f"payload cannot be stored as JSON: {type(e).__name__}") from None
        if len(payload_b) > MAX_PAYLOAD_BYTES:
            raise AuditError("payload too large")
        c = self._db
        c.execute("BEGIN IMMEDIATE")
        try:
            last = c.execute("SELECT seq, event_hash, chain_id FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            if last and last[2] != self.chain_id:
                raise AuditError("store belongs to a different chain_id")
            seq, prev = (last[0] + 1, last[1]) if last else (0, genesis(self.chain_id))
            body = {"chain_id": self.chain_id, "seq": seq, "ts": int(self._clock_us()),
                    "event_type": event_type, "principal": principal, "action": action,
                    "payload_hash": sha(payload_b), "prev_hash": prev, "schema_version": SCHEMA_VERSION}
            ev_hash = sha(canonical(body))
            c.execute("INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (seq, self.chain_id, body["ts"], event_type, principal, action,
                       payload_b.decode("utf-8"), body["payload_hash"], prev, ev_hash, SCHEMA_VERSION))
            c.execute("COMMIT")
        except BaseException:                        # an error, or an interrupt landing inside the write:
            try:                                     # undo the half-made write, so the log is not left stuck
                c.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        return {"seq": seq, "event_hash": ev_hash, "prev_hash": prev}

    # ------------------------------------------------------------- reading
    def events(self, event_type: str | None = None) -> list[dict]:
        out = []
        for r in self._db.execute("SELECT seq, chain_id, ts, event_type, principal, action, payload,"
                                  " payload_hash, prev_hash, event_hash, schema_version FROM audit ORDER BY seq"):
            if event_type and r[3] != event_type:
                continue
            out.append({"seq": r[0], "chain_id": r[1], "ts": r[2], "event_type": r[3], "principal": r[4],
                        "action": r[5], "payload": json.loads(r[6]), "payload_hash": r[7],
                        "prev_hash": r[8], "event_hash": r[9], "schema_version": r[10]})
        return out

    def head(self) -> str | None:
        r = self._db.execute("SELECT event_hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return r[0] if r else None

    def verify(self, expected_head: str | None = None) -> dict:
        """Read-only. Checks sequence density, payload hashes, links and event
        hashes; with `expected_head`, also truncation / whole-chain replacement."""
        rows = self._db.execute("SELECT seq, chain_id, ts, event_type, principal, action, payload,"
                                " payload_hash, prev_hash, event_hash, schema_version FROM audit ORDER BY seq"
                                ).fetchall()
        if not rows:
            ok = expected_head is None
            return {"ok": ok, "events": 0, "error": None if ok else "empty chain cannot match a head"}
        chain_id = rows[0][1]
        if not isinstance(chain_id, str):
            return {"ok": False, "events": len(rows), "error": "unreadable row at seq 0"}
        prev = genesis(chain_id)
        for i, (seq, cid, ts, etype, principal, action, payload, phash, prev_hash, ev_hash, sv) in enumerate(rows):
            def bad(msg):
                return {"ok": False, "events": len(rows), "error": f"{msg} at seq {seq!r}"[:200]}
            try:                                     # a row that cannot even be read is a failed check, not an error
                if cid != chain_id:
                    return bad("chain identity mix")
                if seq != i:
                    return bad("gap or reorder")
                if sv != SCHEMA_VERSION:
                    return bad("unknown schema_version")
                if not all(isinstance(v, str) for v in (etype, principal, action, payload, phash, prev_hash, ev_hash)) \
                        or not isinstance(ts, int):
                    return bad("unreadable row")
                try:
                    pl = json.loads(payload)
                except ValueError:
                    return bad("unreadable payload")
                if canonical(pl).decode("utf-8") != payload or sha(canonical(pl)) != phash:
                    return bad("payload hash mismatch")
                if prev_hash != prev:
                    return bad("broken link")
                body = {"chain_id": cid, "seq": seq, "ts": ts, "event_type": etype, "principal": principal,
                        "action": action, "payload_hash": phash, "prev_hash": prev_hash, "schema_version": sv}
                if sha(canonical(body)) != ev_hash:
                    return bad("event hash mismatch")
            except Exception:  # noqa: BLE001
                return bad("unreadable row")
            prev = ev_hash
        if expected_head is not None and prev != expected_head:
            return {"ok": False, "events": len(rows),
                    "error": "head mismatch (truncation or whole-chain replacement)"}
        return {"ok": True, "events": len(rows), "head": prev, "chain_id": chain_id}
