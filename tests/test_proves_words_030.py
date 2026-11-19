"""0.3.0 (Claude 23:40 Fri 13 Nov 2026, PR #210's review: "a tamper-evident hash chain is incomplete without an externally anchored
audit.head()"): what verify() says it proves must not claim more than the chain can.

  P1  verify() WITHOUT an expected head passes on a log rewritten by someone with write access (every hash recomputed), so its report
      must not say "the record was not altered": it says the links hold and that a rewrite by a writer of the file is NOT ruled out,
      and names the anchor as the way to rule it out
  P2  verify(expected_head=...) that holds says the log is the one whose head was kept
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oathline.audit import AuditLog  # noqa: E402


class P_Proves(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oathline-proves-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = AuditLog(os.path.join(self.dir, "audit.db"), chain_id="proves")
        self.addCleanup(self.log.close)
        for i in range(3):
            self.log.append("EVENT", "alice", "act", {"n": i})

    def test_p1_plain(self):
        r = self.log.verify()
        self.assertTrue(r["ok"])
        self.assertNotIn("was not altered", r["proves"])
        self.assertIn("not ruled out", r["proves"])
        self.assertIn("anchor", r["proves"])

    def test_p2_expected_head(self):
        r = self.log.verify(expected_head=self.log.head())
        self.assertTrue(r["ok"])
        self.assertIn("whose head you kept", r["proves"])


if __name__ == "__main__":
    unittest.main()
