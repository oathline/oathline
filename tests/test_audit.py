import os
import sqlite3
import tempfile
import unittest

from oathline import AuditError, AuditLog


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "audit.db")
        self.log = AuditLog(self.path)
        for i in range(5):
            self.log.append("EVENT", "alice", "demo.action", {"i": i})

    def tearDown(self):
        self.log.close()
        self.tmp.cleanup()

    def tamper(self, sql: str) -> dict:
        """Bypass the triggers as an attacker with file access would, then re-verify."""
        c = sqlite3.connect(self.path)
        c.execute("DROP TRIGGER audit_no_update")
        c.execute("DROP TRIGGER audit_no_delete")
        c.execute(sql)
        c.commit()
        c.close()
        return {}

    def reopen_verify(self, expected_head=None) -> dict:
        with AuditLog(self.path) as log:
            return log.verify(expected_head=expected_head)

    def test_valid_chain(self):
        v = self.log.verify(expected_head=self.log.head())
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 5)

    def test_update_and_delete_blocked(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.log._db.execute("UPDATE audit SET principal='mallory' WHERE seq=1")
        with self.assertRaises(sqlite3.DatabaseError):
            self.log._db.execute("DELETE FROM audit WHERE seq=4")

    def test_edited_payload_detected(self):
        self.tamper("""UPDATE audit SET payload='{"i":99}' WHERE seq=2""")
        self.assertIn("payload hash", self.reopen_verify()["error"])

    def test_deleted_middle_event_detected(self):
        self.tamper("DELETE FROM audit WHERE seq=2")
        self.assertFalse(self.reopen_verify()["ok"])

    def test_truncation_needs_external_head(self):
        head = self.log.head()
        self.tamper("DELETE FROM audit WHERE seq=4")
        self.assertTrue(self.reopen_verify()["ok"])                      # self-consistent...
        self.assertFalse(self.reopen_verify(expected_head=head)["ok"])   # ...but not the anchored head

    def test_principal_change_detected(self):
        self.tamper("UPDATE audit SET principal='mallory' WHERE seq=0")
        self.assertIn("event hash", self.reopen_verify()["error"])

    def test_input_limits(self):
        with self.assertRaises(AuditError):
            self.log.append("", "alice", "x")
        with self.assertRaises(AuditError):
            self.log.append("E", "alice", "x", {"blob": "x" * 20000})

    def test_other_chain_refused(self):
        with AuditLog(self.path, chain_id="another-chain") as other:
            with self.assertRaises(AuditError):
                other.append("EVENT", "alice", "demo.action")


if __name__ == "__main__":
    unittest.main()
