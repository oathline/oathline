"""Tamper sweep: on a 50-event log, as an attacker with file access, change every field of every row,
delete each row, swap each neighbouring pair. verify() must catch every change except the one stated limit."""
import os
import shutil
import sqlite3
import tempfile
import unittest

from oathline import AuditLog

FIELDS = ("seq", "chain_id", "ts", "event_type", "principal", "action", "payload", "payload_hash", "prev_hash",
          "event_hash", "schema_version")
EVENTS = 50


def other(field, value):
    """A different, plausible value for a field."""
    if field == "seq":
        return value + 1000
    if field in ("ts", "schema_version"):
        return value + 1
    if field == "payload":
        return '{"i":"changed"}'
    if field in ("payload_hash", "prev_hash", "event_hash"):
        return ("0" if value[0] != "0" else "1") + value[1:]
    return value + "x"


class TamperSweepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clean = os.path.join(cls.tmp.name, "clean.db")
        with AuditLog(cls.clean) as log:
            for i in range(EVENTS):
                log.append("EVENT", "alice" if i % 2 else "agent", f"demo.action{i % 5}", {"i": i, "note": "n" * (i % 7)})
            cls.head = log.head()
        c = sqlite3.connect(cls.clean)                 # what an attacker with the file does first
        c.execute("DROP TRIGGER audit_no_update")
        c.execute("DROP TRIGGER audit_no_delete")
        c.commit()
        c.close()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def tampered(self, *statements):
        path = os.path.join(self.tmp.name, "work.db")
        shutil.copyfile(self.clean, path)
        c = sqlite3.connect(path)
        for sql, args in statements:
            c.execute(sql, args)
        c.commit()
        c.close()
        with AuditLog(path) as log:
            return log.verify(), log.verify(expected_head=self.head)

    def test_the_untouched_copy_verifies(self):
        plain, anchored = self.tampered()
        self.assertTrue(plain["ok"])
        self.assertTrue(anchored["ok"])
        self.assertEqual(plain["events"], EVENTS)

    def test_every_field_of_every_row(self):
        c = sqlite3.connect(self.clean)
        rows = c.execute("SELECT " + ", ".join(FIELDS) + " FROM audit ORDER BY seq").fetchall()
        c.close()
        checked = 0
        for row in rows:
            for field, value in zip(FIELDS, row):
                plain, anchored = self.tampered((f"UPDATE audit SET {field}=? WHERE seq=?", (other(field, value), row[0])))
                self.assertFalse(plain["ok"], (row[0], field))
                self.assertFalse(anchored["ok"], (row[0], field))
                checked += 1
        self.assertEqual(checked, EVENTS * len(FIELDS))

    def test_delete_each_row(self):
        for seq in range(EVENTS):
            plain, anchored = self.tampered(("DELETE FROM audit WHERE seq=?", (seq,)))
            self.assertFalse(anchored["ok"], seq)                    # always caught against a head kept elsewhere
            if seq < EVENTS - 1:
                self.assertFalse(plain["ok"], seq)                   # a hole, or a missing first event: caught
            else:
                self.assertTrue(plain["ok"])                         # THE LIMIT: the last event cut off

    def test_the_truncation_limit_exactly(self):
        """Cutting any number of events off the END leaves a chain that verifies on its own.
        Only a head hash kept somewhere else catches it. An emptied log verifies on its own too."""
        for keep in (49, 25, 1, 0):
            plain, anchored = self.tampered(("DELETE FROM audit WHERE seq>=?", (keep,)))
            self.assertTrue(plain["ok"], keep)
            self.assertEqual(plain["events"], keep)
            self.assertFalse(anchored["ok"], keep)

    def test_swap_each_neighbouring_pair(self):
        for seq in range(EVENTS - 1):
            plain, anchored = self.tampered(("UPDATE audit SET seq=-1 WHERE seq=?", (seq,)),
                                            ("UPDATE audit SET seq=? WHERE seq=?", (seq, seq + 1)),
                                            ("UPDATE audit SET seq=? WHERE seq=-1", (seq + 1,)))
            self.assertFalse(plain["ok"], seq)
            self.assertFalse(anchored["ok"], seq)

    def test_insert_or_replace_and_an_added_event_are_caught(self):
        plain, _ = self.tampered(("INSERT OR REPLACE INTO audit SELECT seq, chain_id, ts, event_type, 'mallory', action,"
                                  " payload, payload_hash, prev_hash, event_hash, schema_version FROM audit WHERE seq=?",
                                  (10,)))
        self.assertFalse(plain["ok"])
        plain, anchored = self.tampered(("INSERT INTO audit SELECT 50, chain_id, ts, event_type, principal, action,"
                                         " payload, payload_hash, prev_hash, 'f' || substr(event_hash, 2),"
                                         " schema_version FROM audit WHERE seq=?", (49,)))
        self.assertFalse(plain["ok"])
        self.assertFalse(anchored["ok"])


if __name__ == "__main__":
    unittest.main()
