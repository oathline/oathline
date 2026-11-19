"""0.3.0 reads what 0.2.0 wrote (Claude 00:30 Wed: "0.2.0 stores must still verify").

tests/fixtures_020/ holds an audit log and a token store written by the v0.2.0 code itself, as SQL dumps (sqlite iterdump; the
test rebuilds the two files from them), (a clean archive of tag v0.2.0,
scratch script make_020_fixture.py, token clock fixed at 1_700_000_000): five events, one HEARD-capability proposal already
SHOWN (not yet heard), one plain gated proposal pending. On a copy of those files, the 0.3.0 code must:
  K1  verify the log whole, from any event, and against the head 0.2.0 printed
  K2  read the events with since_seq
  K3  hear and confirm the 0.2.0 heard proposal: its items hash (no `carried`) still matches the SHOWN record, so it runs once
  K4  confirm the 0.2.0 plain proposal, and the log still verifies after 0.3.0 appended to it
"""
import os
import shutil
import sqlite3
import tempfile
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures_020")
HEAD = "0c9b6b72ffb6f222db99e45c6b4a5ea9cc79ad7dacfd1ce439f0a94ad1c8d238"
HEARD = "tok_f9739d38a620427eb1ec4c3b41891e33"
PLAIN = "tok_5bb3f7761bde4e43be2ea7a4fc072f3a"


class Compat020(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oathline-compat-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for name in ("audit", "tokens"):
            db = sqlite3.connect(os.path.join(self.dir, name + ".db"))
            with open(os.path.join(FIX, name + ".sql"), encoding="utf-8") as fh:
                db.executescript(fh.read())
            db.close()
        self.audit = AuditLog(os.path.join(self.dir, "audit.db"), chain_id="fixture-020")
        self.addCleanup(self.audit.close)
        self.tokens = TokenStore(os.path.join(self.dir, "tokens.db"), ttl_seconds=300, clock=lambda: 1_700_000_010.0)
        self.addCleanup(self.tokens.close)
        reg = Registry([Capability("sms.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True, heard=True,
                                   products=frozenset({"siya"})),
                        Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
        self.eng = Engine(reg, audit=self.audit, tokens=self.tokens)
        self.ran = []
        self.eng.register("sms.send", lambda a: self.ran.append(("sms", a["to"])) or {"ok": True})
        self.eng.register("email.send", lambda a: self.ran.append(("email", a["to"])) or {"ok": True})
        self.eng.register_renderer("sms.send", lambda k, v: str(v))

    def test_k1_verify(self):
        self.assertTrue(self.audit.verify(HEAD)["ok"])
        for n in range(5):
            self.assertTrue(self.audit.verify(from_seq=n)["ok"], n)

    def test_k2_since(self):
        self.assertEqual([e["seq"] for e in self.audit.events(since_seq=2)], [3, 4])

    def test_k3_heard_proposal_completes(self):
        self.assertTrue(self.eng.hear("siya", HEARD, channel="card")["ok"])
        out = self.eng.confirm("alice", HEARD)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ran, [("sms", "+61400000000")])

    def test_k4_plain_and_verify_after(self):
        self.assertTrue(self.eng.confirm("alice", PLAIN)["ok"])
        self.assertEqual(self.ran, [("email", "bob@example.com")])
        r = self.audit.verify()
        self.assertTrue(r["ok"], r)
        self.assertGreater(r["events"], 5)


if __name__ == "__main__":
    unittest.main()
