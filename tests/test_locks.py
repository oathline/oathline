"""Opening a store to read it must not need a write lock, and nothing may be left open or locked behind."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from oathline import AuditLog, TokenStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOLDER = r"""
import os, sqlite3, sys, time
path, flag, release = sys.argv[1:4]
c = sqlite3.connect(path)
c.execute("BEGIN IMMEDIATE")                       # a write transaction, held until told to let go
open(flag, "w").close()
while not os.path.exists(release):
    time.sleep(0.01)
c.rollback()
c.close()
"""


def seconds(fn):
    started = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - started


class LocksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "audit.db")
        with AuditLog(self.path) as log:
            for i in range(3):
                log.append("EVENT", "alice", "demo", {"i": i})
            self.head = log.head()

    def tearDown(self):
        self.tmp.cleanup()                            # on Windows this fails if anything is still open

    def test_opening_an_existing_log_to_read_it_takes_no_write_lock(self):
        holder = sqlite3.connect(self.path)
        holder.execute("BEGIN IMMEDIATE")              # another connection is in the middle of a write
        try:
            log, took = seconds(lambda: AuditLog(self.path))
            self.assertLess(took, 2)                   # not the 10-second busy wait
            try:
                self.assertTrue(log.verify(expected_head=self.head)["ok"])
                self.assertEqual(log.head(), self.head)
                self.assertEqual(len(log.events()), 3)
            finally:
                log.close()
        finally:
            holder.rollback()
            holder.close()

    def test_opening_a_log_whose_triggers_were_removed_still_writes_nothing(self):
        """The case the sweep tests hit: an attacker removed the triggers, a writer holds the file, and a
        reader opens the log to verify it. Re-creating the triggers on open would need a write lock."""
        c = sqlite3.connect(self.path)
        c.execute("DROP TRIGGER audit_no_update")
        c.execute("DROP TRIGGER audit_no_delete")
        c.commit()
        c.close()
        holder = sqlite3.connect(self.path)
        holder.execute("BEGIN IMMEDIATE")
        try:
            log, took = seconds(lambda: AuditLog(self.path))
            self.assertLess(took, 2)
            try:
                self.assertTrue(log.verify(expected_head=self.head)["ok"])
            finally:
                log.close()
        finally:
            holder.rollback()
            holder.close()
        with AuditLog(self.path) as log:                   # and nothing was put back behind the attacker's back
            names = {r[0] for r in log._db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        self.assertEqual(names, set())

    def test_the_table_and_its_triggers_are_created_in_one_transaction(self):
        # watch the create script itself: the statements the new log's own connection ran
        traced = []
        new = os.path.join(self.tmp.name, "new2.db")
        original = sqlite3.connect
        def connect(*a, **kw):
            c = original(*a, **kw)
            c.set_trace_callback(traced.append)
            return c
        sqlite3.connect = connect
        try:
            AuditLog(new).close()
        finally:
            sqlite3.connect = original
        creates = [s for s in traced if s.upper().startswith(("BEGIN", "COMMIT", "CREATE"))]
        self.assertEqual([s.split()[0].upper().rstrip(";") for s in creates],
                         ["BEGIN", "CREATE", "CREATE", "CREATE", "COMMIT"])
        self.assertIn("IMMEDIATE", creates[0].upper())

    def test_a_half_made_empty_log_gets_its_triggers_on_open(self):
        """The state a kill part-way through creation could leave: the table is there, the triggers are not,
        and nothing has been written. Opening it finishes the job."""
        path = os.path.join(self.tmp.name, "half.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE audit(seq INTEGER PRIMARY KEY, chain_id TEXT NOT NULL, ts INTEGER NOT NULL,"
                  " event_type TEXT NOT NULL, principal TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL,"
                  " payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE,"
                  " schema_version INTEGER NOT NULL)")
        c.commit()
        c.close()
        with AuditLog(path) as log:
            names = {r[0] for r in log._db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertEqual(names, {"audit_no_update", "audit_no_delete"})
            log.append("EVENT", "alice", "demo", {})
            with self.assertRaises(sqlite3.DatabaseError):
                log._db.execute("DELETE FROM audit")

    def test_a_log_with_rows_and_no_triggers_is_left_as_it_is(self):
        """The state an attacker leaves: rows present, triggers removed. Opening it changes nothing;
        verify() with a head kept elsewhere is the guarantee, not the triggers."""
        c = sqlite3.connect(self.path)
        c.execute("DROP TRIGGER audit_no_update")
        c.execute("DROP TRIGGER audit_no_delete")
        c.commit()
        c.close()
        with AuditLog(self.path) as log:
            names = {r[0] for r in log._db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
            self.assertEqual(names, set())
            self.assertTrue(log.verify(expected_head=self.head)["ok"])

    def test_a_second_process_can_verify_while_the_first_holds_a_write_transaction(self):
        flag, release = os.path.join(self.tmp.name, "holding"), os.path.join(self.tmp.name, "release")
        child = subprocess.Popen([sys.executable, "-c", HOLDER, self.path, flag, release], cwd=ROOT)
        try:
            for _ in range(500):
                if os.path.exists(flag):
                    break
                time.sleep(0.01)
            self.assertTrue(os.path.exists(flag), "the holder did not start")
            (log, took) = seconds(lambda: AuditLog(self.path))
            self.assertLess(took, 2)
            try:
                out, took = seconds(lambda: log.verify(expected_head=self.head))
                self.assertTrue(out["ok"])
                self.assertLess(took, 2)
            finally:
                log.close()
        finally:
            open(release, "w").close()
            child.wait(timeout=30)

    def test_a_failed_open_leaves_no_connection_behind(self):
        bad = os.path.join(self.tmp.name, "not-a-database.db")
        with open(bad, "wb") as fh:
            fh.write(b"this is not an SQLite file" * 100)
        with self.assertRaises(sqlite3.DatabaseError):
            AuditLog(bad)
        with self.assertRaises(sqlite3.DatabaseError):
            TokenStore(bad)
        os.remove(bad)                                 # on Windows this fails while a connection still holds it

    def test_readers_leave_no_lock_behind(self):
        log = AuditLog(self.path)
        try:
            log.head()
            log.verify()
            log.events()
            log.events("EVENT")
            other = sqlite3.connect(self.path, timeout=0.2)
            try:
                other.execute("BEGIN EXCLUSIVE")       # refused at once if any read lock were still held
                other.rollback()
            finally:
                other.close()
        finally:
            log.close()

    def test_opening_an_existing_token_store_takes_no_write_lock(self):
        path = os.path.join(self.tmp.name, "tokens.db")
        first = TokenStore(path)
        tok = first.propose("alice", "x.do", {"a": 1})
        first.close()
        holder = sqlite3.connect(path)
        holder.execute("BEGIN IMMEDIATE")
        try:
            store, took = seconds(lambda: TokenStore(path))
            self.assertLess(took, 2)
            try:
                self.assertEqual(store.get(tok)["state"], "proposed")
            finally:
                store.close()
        finally:
            holder.rollback()
            holder.close()


if __name__ == "__main__":
    unittest.main()
