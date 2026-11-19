"""0.3.0 step 1 (CORE_0.3.0_NOTES section 3): read the audit log from a sequence number.

0.2.0 already numbers every event (`seq`, dense from 0, inside the hashed body; verify() fails a gap or a reorder). What a reader
lacked is a way to start after the last event it read without walking from the start:

  S1  AuditLog.events(since_seq=N) returns only the events with seq > N, in order; with event_type too, both filters apply
  S2  AuditLog.verify(from_seq=N) checks the links from event N onwards and reports the events it checked; with from_hash it
      also requires event N's hash to be the one the reader kept (a rewritten earlier event is then caught)
  S3  a tampered event after N fails verify(from_seq=N); a tampered event at or before N is outside it unless from_hash is given
  S4  a 0.2.0 log (written by 0.2.0's code path, unchanged) verifies whole and from any N
  S5  the CLI, read-only: `python -m oathline events <db> --since N` prints one JSON line per event after N;
      `python -m oathline verify <db> [--since N] [--head H]` prints the report and exits 0 when it holds, 1 when not
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oathline.audit import AuditLog  # noqa: E402


class _Log(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="oathline-since-")
        self.path = os.path.join(self.dir, "audit.db")
        t = iter(range(1000, 100000))
        self.log = AuditLog(self.path, chain_id="since-test", clock_us=lambda: next(t))
        for i in range(6):
            self.log.append("EVENT" if i % 2 == 0 else "OTHER", "alice", "act", {"n": i})

    def tearDown(self):
        self.log.close()

    def tamper(self, seq):
        db = sqlite3.connect(self.path)
        db.execute("DROP TRIGGER audit_no_update")
        db.execute("UPDATE audit SET payload=? WHERE seq=?", (json.dumps({"n": 99}, separators=(",", ":")), seq))
        db.commit()
        db.close()


class S1_EventsSince(_Log):
    def test_s1_since(self):
        self.assertEqual([e["seq"] for e in self.log.events(since_seq=3)], [4, 5])
        self.assertEqual([e["seq"] for e in self.log.events(since_seq=-1)], [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.log.events(since_seq=5), [])
        self.assertEqual([e["seq"] for e in self.log.events("EVENT", since_seq=1)], [2, 4])

    def test_s1_bad_since(self):
        for bad in ("3", 1.5, True):
            with self.assertRaises(TypeError):
                self.log.events(since_seq=bad)


class S2_VerifyFrom(_Log):
    def test_s2_from(self):
        whole = self.log.verify()
        self.assertTrue(whole["ok"])
        r = self.log.verify(from_seq=3)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["events"], 2)
        self.assertEqual(r["head"], whole["head"])
        self.assertEqual(r["from_seq"], 3)

    def test_s2_from_hash(self):
        h3 = self.log.events()[3]["event_hash"]
        self.assertTrue(self.log.verify(from_seq=3, from_hash=h3)["ok"])
        r = self.log.verify(from_seq=3, from_hash="0" * 64)
        self.assertFalse(r["ok"])
        self.assertIn("from_hash", r["error"])

    def test_s2_from_last_and_beyond(self):
        self.assertEqual(self.log.verify(from_seq=5)["events"], 0)
        r = self.log.verify(from_seq=9)
        self.assertFalse(r["ok"])
        self.assertIn("no event", r["error"])


class S3_Tamper(_Log):
    def test_s3_after_from(self):
        self.tamper(4)
        r = self.log.verify(from_seq=3)
        self.assertFalse(r["ok"])
        self.assertIn("seq 4", r["error"])

    def test_s3_before_from(self):
        h1 = self.log.events()[1]["event_hash"]
        self.tamper(1)
        self.assertTrue(self.log.verify(from_seq=3)["ok"])        # outside the checked range: the reader trusts what it read
        self.assertFalse(self.log.verify()["ok"])
        r = self.log.verify(from_seq=1, from_hash=h1)              # the kept hash of event 1 no longer matches its content
        self.assertFalse(r["ok"])


class S4_OldLogs(_Log):
    def test_s4_every_from(self):
        for n in range(6):
            self.assertTrue(self.log.verify(from_seq=n)["ok"], n)


class S5_Cli(_Log):
    def run_cli(self, *args):
        self.log.close()
        env = dict(os.environ, PYTHONPATH=ROOT)
        p = subprocess.run([sys.executable, "-m", "oathline", *args], capture_output=True, text=True, env=env, cwd=self.dir,
                           timeout=60)
        self.log = AuditLog(self.path, chain_id="since-test")
        return p

    def test_s5_events_since(self):
        p = self.run_cli("events", self.path, "--since", "3")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
        self.assertEqual([r["seq"] for r in rows], [4, 5])

    def test_s5_verify(self):
        p = self.run_cli("verify", self.path)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["ok"])
        p = self.run_cli("verify", self.path, "--since", "2")
        self.assertEqual(json.loads(p.stdout)["events"], 3)
        p = self.run_cli("verify", self.path, "--head", "0" * 64)
        self.assertEqual(p.returncode, 1)

    def test_s5_cli_is_read_only(self):
        before = os.path.getmtime(self.path), os.path.getsize(self.path)
        self.run_cli("verify", self.path)
        self.run_cli("events", self.path)
        self.assertEqual((os.path.getmtime(self.path), os.path.getsize(self.path)), before)

    def test_s5_from_hash_needs_since(self):
        p = self.run_cli("verify", self.path, "--from-hash", "0" * 64)
        self.assertEqual(p.returncode, 2)
        self.assertIn("--from-hash needs --since", p.stdout + p.stderr)

    def test_s5_missing_file(self):
        p = self.run_cli("verify", os.path.join(self.dir, "nope.db"))
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "nope.db")))


if __name__ == "__main__":
    unittest.main()
