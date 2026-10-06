"""Crash: the process dies after the "about to run" record and before the result is recorded."""
import os
import subprocess
import sys
import tempfile
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def engine(folder, executor):
    reg = Registry([Capability("db.migrate", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg, audit=AuditLog(os.path.join(folder, "audit.db")),
                 tokens=TokenStore(os.path.join(folder, "tokens.db")))
    eng.register("db.migrate", executor)
    return eng


CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from tests.test_crash import engine
folder = sys.argv[2]

def dies(a):
    with open(os.path.join(folder, "effects.txt"), "a", encoding="utf-8") as fh:
        fh.write("started\n")
    os._exit(7)                                   # the process is gone: no result, no cleanup

eng = engine(folder, dies)
token = eng.request("agent", "db.migrate", {"step": "1"})["token"]
with open(os.path.join(folder, "token.txt"), "w", encoding="utf-8") as fh:
    fh.write(token)
eng.confirm("alice", token)
"""


class CrashTest(unittest.TestCase):
    def test_a_crash_mid_action_leaves_a_verifiable_log_a_spent_token_and_a_visible_unfinished_action(self):
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.run([sys.executable, "-c", CHILD, ROOT, folder], cwd=ROOT,
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(child.returncode, 7, child.stderr)
            with open(os.path.join(folder, "token.txt"), encoding="utf-8") as fh:
                token = fh.read()
            ran_again = []
            eng = engine(folder, lambda a: ran_again.append(1) or {"done": True})
            try:
                self.assertTrue(eng.audit.verify()["ok"])                       # the chain verifies on reopen
                events = eng.audit.events()
                self.assertEqual(events[-1]["event_type"], "EXECUTING")         # the last word is "about to run"
                self.assertEqual(eng.audit.events("EXECUTED"), [])
                unfinished = eng.unfinished()
                self.assertEqual(len(unfinished), 1)
                self.assertEqual((unfinished[0]["action"], unfinished[0]["principal"], unfinished[0]["seq"]),
                                 ("db.migrate", "agent", events[-1]["seq"]))
                self.assertEqual(unfinished[0]["payload"]["confirmed_by"], "alice")
                out = eng.confirm("alice", token)                               # the token cannot be used again
                self.assertEqual((out["ok"], out["error"]), (False, "already_used"))
                self.assertEqual(ran_again, [])
                with open(os.path.join(folder, "effects.txt"), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "started\n")
                self.assertEqual(len(eng.unfinished()), 1)                      # still shown as unfinished
                self.assertTrue(eng.audit.verify()["ok"])
            finally:
                eng.audit.close()
                eng.tokens.close()

    def test_finished_actions_are_not_listed_as_unfinished(self):
        with tempfile.TemporaryDirectory() as folder:
            eng = engine(folder, lambda a: {"done": True})
            try:
                tok = eng.request("agent", "db.migrate", {})["token"]
                self.assertTrue(eng.confirm("alice", tok)["ok"])
                self.assertEqual(eng.unfinished(), [])
                executing = eng.audit.events("EXECUTING")[0]
                executed = eng.audit.events("EXECUTED")[0]
                self.assertEqual(executed["payload"]["executing_seq"], executing["seq"])
            finally:
                eng.audit.close()
                eng.tokens.close()


if __name__ == "__main__":
    unittest.main()
