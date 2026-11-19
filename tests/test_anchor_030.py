"""0.3.0 (Claude 22:18 Fri 13 Nov 2026, after PR #210's review: "a tamper-evident hash chain is incomplete without an externally
anchored audit.head()"): anchor the head OUTSIDE the machine, and check the log against it later.

  A1  `python -m oathline anchor --store PATH` prints {"head", "seq", "events", "chain_id"} (read-only; a missing file is exit 2,
      never created)
  A2  `AuditLog.verify_anchor(head, seq)` and `python -m oathline verify --store PATH --expected-head SHA --anchor-seq N`:
      (i)   the log untouched since the anchor -> ok (exit 0)
      (ii)  the log GROWN since the anchor -> ok: the anchored head is still in the chain at its seq (an anchor proves the past up to
            its seq, nothing after it)
      (iii) the log TRUNCATED before the anchored seq -> exit 1, "the log is shorter than the anchor", while verify() alone passes
      (iv)  the log REWRITTEN with every later hash recomputed (a full privileged rewrite) -> exit 1, while verify() alone passes
  A3  `verify --expected-head SHA` without --anchor-seq keeps its 0.3.0 meaning (the head must be the CURRENT head); `--store` is
      accepted beside the positional path
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oathline.__main__ import main  # noqa: E402
from oathline.audit import AuditLog  # noqa: E402


def cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    text = out.getvalue().strip()
    return code, (json.loads(text.splitlines()[-1]) if text else {})


class _Log(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oathline-anchor-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "audit.db")
        self.log = AuditLog(self.path, chain_id="anchor-test")
        for i in range(8):
            self.log.append("EVENT", "alice", "act", {"n": i})
        self.log.close()

    def anchor(self):
        code, out = cli("anchor", "--store", self.path)
        self.assertEqual(code, 0, out)
        return out


class A1_Anchor(_Log):
    def test_a1(self):
        out = self.anchor()
        log = AuditLog(self.path, read_only=True)
        try:
            self.assertEqual((out["head"], out["seq"], out["events"], out["chain_id"]), (log.head(), 7, 8, "anchor-test"))
        finally:
            log.close()
        missing = os.path.join(self.dir, "nope.db")
        code, _ = cli("anchor", "--store", missing)
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))


class A2_VerifyAnchor(_Log):
    def check(self, head, seq):
        return cli("verify", "--store", self.path, "--expected-head", head, "--anchor-seq", str(seq))

    def test_a2_untouched(self):
        a = self.anchor()
        self.assertEqual(self.check(a["head"], a["seq"])[0], 0)

    def test_a2_grown(self):
        a = self.anchor()
        log = AuditLog(self.path, chain_id="anchor-test")
        log.append("EVENT", "alice", "act", {"n": 99})
        log.close()
        code, out = self.check(a["head"], a["seq"])
        self.assertEqual(code, 0, out)

    def test_a2_truncated(self):
        a = self.anchor()
        db = sqlite3.connect(self.path)
        db.execute("DROP TRIGGER IF EXISTS audit_no_delete")          # a privileged writer removes the append-only guard first
        db.execute("DELETE FROM audit WHERE seq > 4")
        db.commit()
        db.close()
        log = AuditLog(self.path, read_only=True)
        try:
            self.assertTrue(log.verify()["ok"])                       # the chain alone still holds: only the anchor catches it
        finally:
            log.close()
        code, out = self.check(a["head"], a["seq"])
        self.assertEqual(code, 1)
        self.assertIn("shorter than the anchor", out.get("error", ""))

    def test_a2_rewritten(self):
        a = self.anchor()
        os.remove(self.path)                                           # a privileged rewrite: the whole chain rebuilt, hashes recomputed
        log = AuditLog(self.path, chain_id="anchor-test")
        for i in range(8):
            log.append("EVENT", "alice", "act", {"n": i * 10})
        log.close()
        log = AuditLog(self.path, read_only=True)
        try:
            self.assertTrue(log.verify()["ok"])
        finally:
            log.close()
        code, out = self.check(a["head"], a["seq"])
        self.assertEqual(code, 1)
        self.assertIn("rewritten", out.get("error", ""))

    def test_a2_api(self):
        a = self.anchor()
        log = AuditLog(self.path, read_only=True)
        try:
            self.assertTrue(log.verify_anchor(a["head"], a["seq"])["ok"])
            self.assertFalse(log.verify_anchor("0" * 64, a["seq"])["ok"])
        finally:
            log.close()


class A4_MistypedAnchor(_Log):
    """FABLE 00:35 must-fix 1: a typing slip is never reported as a rewritten log. A head that is not 64 lower-case hex characters or a
    seq below 0 is refused before the log is read: one JSON line, exit 2, no traceback; a well-formed head that does not match says
    both readings."""
    def run_cli(self, head, seq):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["verify", "--store", self.path, "--expected-head", head, "--anchor-seq", str(seq)])
        lines = out.getvalue().strip().splitlines()
        return code, lines

    def test_a4_cli(self):
        a = self.anchor()
        for head, seq in (("notahash", a["seq"]), (a["head"][:63], a["seq"]), (a["head"].upper(), a["seq"]), (a["head"], -1)):
            code, lines = self.run_cli(head, seq)
            self.assertEqual(code, 2, (head, seq, lines))
            self.assertEqual(len(lines), 1, lines)
            out = json.loads(lines[0])
            self.assertFalse(out["ok"])
            self.assertNotIn("rewritten", out["error"])
        code, lines = self.run_cli("a" * 64, a["seq"])
        self.assertEqual(code, 1)
        self.assertIn(f"does not carry the anchored head: the log was rewritten up to it, or this is not the head that was kept for "
                      f"seq {a['seq']}", json.loads(lines[-1])["error"])

    def test_a4_plain_head(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["verify", "--store", self.path, "--expected-head", "notahash"])
        self.assertEqual(code, 2)
        self.assertNotIn("truncation", out.getvalue())

    def test_a4_api(self):
        a = self.anchor()
        log = AuditLog(self.path, read_only=True)
        try:
            for head, seq in (("notahash", a["seq"]), (a["head"], -1)):
                r = log.verify_anchor(head, seq)
                self.assertFalse(r["ok"])
                self.assertEqual(r.get("refused"), "argument", r)
                self.assertNotIn("rewritten", r["error"])
            r = log.verify_anchor("a" * 64, a["seq"])
            self.assertIn("or this is not the head that was kept", r["error"])
        finally:
            log.close()


class A3_PlainExpectedHead(_Log):
    def test_a3(self):
        a = self.anchor()
        self.assertEqual(cli("verify", self.path, "--expected-head", a["head"])[0], 0)
        log = AuditLog(self.path, chain_id="anchor-test")
        log.append("EVENT", "alice", "act", {"n": 99})
        log.close()
        self.assertEqual(cli("verify", "--store", self.path, "--expected-head", a["head"])[0], 1)   # without --anchor-seq: the CURRENT head


if __name__ == "__main__":
    unittest.main()
