"""0.3.0 (FABLE 01:31 Sat 14 Nov 2026, the stranger test's re-run): the last slips of the command line.

  L1a `verify --since N --from-hash <not a hash>` (and `verify(from_hash=...)`) is refused as an argument: exit 2 / refused, never "rewritten"
  L1b `--anchor-seq` typed too high: "shorter than the anchor" also says "or this is not the seq that was kept"
  L1c plain `--expected-head <a head kept earlier>` on a log that has only GROWN: the mismatch also says "or events were added since this
      head was kept: check with --anchor-seq"
  L2  a store path written with `~` is found when the shell did not expand it (cmd, PowerShell); a missing one still exits 2, nothing created
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oathline.__main__ import main  # noqa: E402
from oathline.audit import AuditLog  # noqa: E402


def cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(argv))
    lines = out.getvalue().strip().splitlines()
    return code, (json.loads(lines[-1]) if lines else {}), lines


class _Log(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oathline-slips-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "audit.db")
        log = AuditLog(self.path, chain_id="slips")
        for i in range(30):
            log.append("EVENT", "alice", "act", {"n": i})
        log.close()


class L1_Slips(_Log):
    def test_l1a_from_hash(self):
        code, out, lines = cli("verify", self.path, "--since", "20", "--from-hash", "nothash")
        self.assertEqual(code, 2)
        self.assertEqual(len(lines), 1)
        self.assertNotIn("rewritten", out["error"])
        log = AuditLog(self.path, read_only=True)
        try:
            r = log.verify(from_seq=20, from_hash="nothash")
            self.assertFalse(r["ok"])
            self.assertEqual(r.get("refused"), "argument")
        finally:
            log.close()

    def test_l1b_seq_too_high(self):
        code, out, _ = cli("anchor", "--store", self.path)
        code, out, _ = cli("verify", "--store", self.path, "--expected-head", out["head"], "--anchor-seq", "99")
        self.assertEqual(code, 1)
        self.assertIn("or this is not the seq that was kept", out["error"])

    def test_l1c_grown(self):
        code, a, _ = cli("anchor", "--store", self.path)
        log = AuditLog(self.path, chain_id="slips")
        log.append("EVENT", "alice", "act", {"n": 99})
        log.close()
        code, out, _ = cli("verify", "--store", self.path, "--expected-head", a["head"])
        self.assertEqual(code, 1)
        self.assertIn("or events were added since this head was kept: check with --anchor-seq", out["error"])


class L2_Tilde(_Log):
    def test_l2(self):
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, "x"))
        shutil.copy(self.path, os.path.join(home, "x", "audit.db"))
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            for cmd in (["anchor", "--store", "~/x/audit.db"], ["verify", "~/x/audit.db"], ["events", "~/x/audit.db", "--since", "28"]):
                code, out, lines = cli(*cmd)
                self.assertEqual(code, 0, (cmd, lines))
            code, out, _ = cli("anchor", "--store", "~/x/missing.db")
            self.assertEqual(code, 2)
            self.assertFalse(os.path.exists(os.path.join(home, "x", "missing.db")))


if __name__ == "__main__":
    unittest.main()
