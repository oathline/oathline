"""Race: many confirmers, one token, file-backed stores. The executor runs exactly once."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def registry():
    return Registry([Capability("pay.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])


def engine(folder, effects):
    eng = Engine(registry(), audit=AuditLog(os.path.join(folder, "audit.db")),
                 tokens=TokenStore(os.path.join(folder, "tokens.db")))

    def pay(a):
        with open(effects, "a", encoding="utf-8") as fh:
            fh.write("paid\n")
        return {"paid": True}

    eng.register("pay.send", pay)
    return eng


CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tests.test_race import engine
folder, token = sys.argv[2], sys.argv[3]
eng = engine(folder, os.path.join(folder, "effects.txt"))
while not os.path.exists(os.path.join(folder, "go")):
    time.sleep(0.001)
out = eng.confirm("alice", token)
print("ok" if out["ok"] else out["error"])
"""


class RaceTest(unittest.TestCase):
    def test_twenty_threads_confirm_one_token_and_it_runs_once(self):
        for _ in range(3):
            with tempfile.TemporaryDirectory() as folder:
                effects = os.path.join(folder, "effects.txt")
                first = engine(folder, effects)
                token = first.request("agent", "pay.send", {"amount": "10"})["token"]
                first.audit.close()
                first.tokens.close()
                results, errors = [], []
                start = threading.Barrier(20)

                def worker():
                    try:
                        eng = engine(folder, effects)            # each thread has its own connections
                        start.wait()
                        results.append(eng.confirm("alice", token))
                        eng.audit.close()
                        eng.tokens.close()
                    except Exception as e:  # noqa: BLE001
                        errors.append(repr(e))

                threads = [threading.Thread(target=worker) for _ in range(20)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                self.assertEqual(errors, [])
                self.assertEqual(len(results), 20)
                self.assertEqual(sum(1 for r in results if r["ok"]), 1)
                self.assertEqual({r["error"] for r in results if not r["ok"]}, {"already_used"})
                with open(effects, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "paid\n")
                with AuditLog(os.path.join(folder, "audit.db")) as log:
                    self.assertTrue(log.verify()["ok"])
                    self.assertEqual(len(log.events("EXECUTED")), 1)
                    self.assertEqual(len(log.events("CONFIRM_REFUSED")), 19)

    def test_two_processes_confirm_one_token_and_it_runs_once(self):
        with tempfile.TemporaryDirectory() as folder:
            effects = os.path.join(folder, "effects.txt")
            first = engine(folder, effects)
            token = first.request("agent", "pay.send", {"amount": "10"})["token"]
            first.audit.close()
            first.tokens.close()
            children = [subprocess.Popen([sys.executable, "-c", CHILD, ROOT, folder, token],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
                        for _ in range(2)]
            with open(os.path.join(folder, "go"), "w", encoding="utf-8") as fh:
                fh.write("go")
            outs = [c.communicate(timeout=60) for c in children]
            self.assertEqual([e for _, e in outs], ["", ""])
            self.assertEqual(sorted(o.strip() for o, _ in outs), ["already_used", "ok"])
            with open(effects, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "paid\n")
            with AuditLog(os.path.join(folder, "audit.db")) as log:
                self.assertTrue(log.verify()["ok"])
                self.assertEqual(len(log.events("EXECUTED")), 1)


if __name__ == "__main__":
    unittest.main()
