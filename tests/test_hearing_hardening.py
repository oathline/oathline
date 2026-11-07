"""The hearing rule under pressure (0.2.0): a locked log at show, hear and correct; a crash between the hearing
and the yes; thousands of random shapes through the four calls; a correction racing a confirmation. These prove
the rule holds; none of them changed the code (they were green on arrival and are kept as the attack table's proof)."""
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore
from oathline import audit as audit_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITEMS = [{"key": "to", "value": "bob@example.com", "label": "To"}, {"key": "amount", "value": 2400000, "label": "Amount"}]
TEXT = "I'll pay bob at example dot com two point four million. Right?"
WORDS = {("to", "bob@example.com"): "bob at example dot com", ("amount", 2400000): "two point four million",
         ("amount", 2500000): "two point five million", ("amount", 2600000): "two point six million",
         ("amount", 9900000): "nine point nine million"}


def words(key, value):
    """The test renderer: how this product says its values (Core renders; the proposer never passes `said`)."""
    if isinstance(value, list):
        return " and ".join(str(v) for v in value)
    return WORDS.get((key, value), str(value))


def registry():
    return Registry([Capability("pay.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True, heard=True,
                                products=frozenset({"siya"}))])


def engine(folder, effects=None):
    eng = Engine(registry(), audit=AuditLog(os.path.join(folder, "audit.db")), tokens=TokenStore(os.path.join(folder, "tokens.db")))

    def pay(a):
        if effects:
            with open(effects, "a", encoding="utf-8") as fh:
                fh.write(f"paid {a.get('amount', '')}\n")
        return {"paid": True}

    eng.register("pay.send", pay)
    eng.register_renderer("pay.send", words)
    return eng


class LockedLogTest(unittest.TestCase):
    """Another connection holds the audit file's write lock; the busy wait is short. Nothing raises, nothing is half done."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.folder.name, "audit.db")
        self.old_wait = audit_module.BUSY_TIMEOUT_SECONDS
        audit_module.BUSY_TIMEOUT_SECONDS = 0.2
        self.holder = None
        self.eng = engine(self.folder.name)

    def tearDown(self):
        self.release_lock()
        self.eng.close()
        audit_module.BUSY_TIMEOUT_SECONDS = self.old_wait
        self.folder.cleanup()

    def hold_lock(self):
        self.holder = sqlite3.connect(self.audit_path, isolation_level=None, timeout=1)
        self.holder.execute("BEGIN IMMEDIATE")

    def release_lock(self):
        if self.holder is not None:
            try:
                self.holder.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            self.holder.close()
            self.holder = None

    def test_locked_log_at_show_hear_and_correct(self):
        token = self.eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.hold_lock()
        out = self.eng.show("siya", token, TEXT, channel="spoken")
        self.assertEqual((out["ok"], out["error"]), (False, "audit_write_failed"))
        self.release_lock()
        self.assertEqual(self.eng.hear("siya", token, channel="spoken")["error"], "not_shown")   # nothing was shown
        self.assertTrue(self.eng.show("siya", token, TEXT, channel="spoken")["ok"])              # the retry works
        self.hold_lock()
        self.assertEqual(self.eng.hear("siya", token, channel="spoken")["error"], "audit_write_failed")
        self.release_lock()
        self.assertEqual(self.eng.confirm("alice", token)["error"], "unheard")                   # nothing was heard
        self.assertTrue(self.eng.hear("siya", token, channel="spoken")["ok"])
        self.hold_lock()
        out = self.eng.correct("siya", token, "amount", 2500000)
        self.assertEqual((out["ok"], out["error"]), (False, "audit_write_failed"))
        self.release_lock()
        self.assertEqual(self.eng.tokens.get(token)["state"], "proposed")                        # the old one stands
        pending = [r[0] for r in self.eng.tokens._db.execute("SELECT id FROM proposals WHERE state='proposed'")]
        self.assertEqual(pending, [token])                                                       # no stray new proposal
        self.assertEqual([e["event_type"] for e in self.eng.audit.events() if e["event_type"] in ("WITHDRAWN", "PROPOSED_BY_AGENT")],
                         ["PROPOSED_BY_AGENT"])
        self.assertTrue(self.eng.confirm("alice", token)["ok"])                                  # the hearing on record still holds
        self.assertTrue(self.eng.audit.verify()["ok"])


CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from tests.test_hearing_hardening import engine, ITEMS, TEXT
folder = sys.argv[2]
eng = engine(folder)
token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
assert eng.show("siya", token, TEXT, channel="spoken")["ok"]
assert eng.hear("siya", token, channel="spoken")["ok"]
with open(os.path.join(folder, "token.txt"), "w", encoding="utf-8") as fh:
    fh.write(token)
os._exit(7)                                       # gone between the hearing and the yes
"""


class CrashBetweenHearingAndYesTest(unittest.TestCase):
    def test_a_hearing_survives_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            child = subprocess.run([sys.executable, "-c", CHILD, ROOT, folder], cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(child.returncode, 7, child.stderr)
            with open(os.path.join(folder, "token.txt"), encoding="utf-8") as fh:
                token = fh.read().strip()
            effects = os.path.join(folder, "effects.txt")
            eng = engine(folder, effects)
            try:
                self.assertEqual(eng.tokens.get(token)["state"], "proposed")
                self.assertEqual([e["event_type"] for e in eng.audit.events()][-2:], ["SHOWN", "HEARD"])
                out = eng.confirm("alice", token)
                self.assertEqual(out["state"], "executed", out)
                self.assertEqual(eng.confirm("alice", token)["error"], "already_used")
                with open(effects, encoding="utf-8") as fh:
                    self.assertEqual(fh.read().count("paid"), 1)
                self.assertTrue(eng.audit.verify()["ok"])
                self.assertEqual(eng.unheard_runs(), [])
            finally:
                eng.close()


class FuzzTheHearingTest(unittest.TestCase):
    """Seeded random shapes through show(), hear(), correct() and confirm(): nothing raises, a heard write never runs
    without a HEARD of exactly its items before it, and the chain verifies."""

    SEED, SHAPES = 20261107, 4000
    NAMES = ["agent", "alice", "mallory", "model", "siya", "", " alice", "a" * 200, None, 7]
    TEXTS = [TEXT, "I'll pay bob at example dot com. Right?", "", "x" * 20000, None, 3, TEXT + " two point five million"]
    CHANNELS = ["spoken", "screen", "card", "telepathy", "", None]
    VALUES = [2500000, "two", None, float("nan"), b"b", {"a": 1}, [1, 2], "x" * 9000, 2400000]

    def test_four_thousand_shapes_through_the_hearing(self):
        rng = random.Random(self.SEED)
        eng = Engine(registry())
        runs = []
        eng.register("pay.send", lambda a: runs.append(dict(a)) or {"paid": True})
        eng.register_renderer("pay.send", words)
        tokens = []
        for i in range(self.SHAPES):
            move = rng.randrange(6)
            tok = (tokens[-1] if rng.random() < 0.7 else rng.choice(tokens)) if tokens and rng.random() < 0.9 \
                else rng.choice(["tok_" + "0" * 32, "nope", None, 5])
            product = rng.choice(["siya"] * 6 + self.NAMES)                      # mostly the product, sometimes anyone
            text = rng.choice([TEXT] * 4 + self.TEXTS)                           # mostly the right words, sometimes junk
            channel = rng.choice(["spoken"] * 4 + self.CHANNELS)
            try:
                if move == 0 or not tokens:
                    out = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS if rng.random() < 0.8 else None)
                    if out.get("ok"):
                        tokens.append(out["token"])
                elif move == 1:
                    eng.show(product, tok, text, channel=channel)
                elif move == 2:
                    eng.hear(product, tok, channel=channel)
                elif move == 3:
                    out = eng.correct(product, tok, rng.choice(["amount", "to", "colour", "", None]), rng.choice(self.VALUES))
                    if out.get("ok"):
                        tokens.append(out["token"])
                else:
                    eng.confirm(rng.choice(["alice"] * 4 + self.NAMES), tok)      # mostly the human, sometimes anyone
            except Exception as exc:  # noqa: BLE001
                self.fail(f"raised at shape {i}: {type(exc).__name__}: {exc}")
            if i % 500 == 499:
                self.assertTrue(eng.audit.verify()["ok"], i)
        self.assertTrue(runs, "the fuzz never reached a run; the shapes are too thin")
        self.assertEqual(eng.unheard_runs(), [])
        heard, executed = {}, []
        for e in eng.audit.events():
            p = e["payload"]
            if e["event_type"] == "HEARD":
                heard.setdefault(p["token"], []).append(p["items_hash"])
            if e["event_type"] == "EXECUTING":
                executed.append(p)
        self.assertEqual(len(executed), len(runs))
        for p in executed:
            self.assertIn(p["items_hash"], heard.get(p["token"], []), p)
        eng.close()


class CorrectionRacesConfirmationTest(unittest.TestCase):
    """One corrects while twelve confirm the heard token, each on its own engine over the same files. Either the old
    items ran exactly once or the correction won and nothing ran; the new items never run unheard; never both."""

    def test_a_correction_and_a_confirm_race(self):
        for round_ in range(3):
            with tempfile.TemporaryDirectory() as folder:
                effects = os.path.join(folder, "effects.txt")
                setup = engine(folder, effects)
                token = setup.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
                self.assertTrue(setup.show("siya", token, TEXT, channel="spoken")["ok"])
                self.assertTrue(setup.hear("siya", token, channel="spoken")["ok"])
                old_hash = setup.tokens.get(token)["items_hash"]
                setup.close()
                results, go = [], threading.Event()

                def confirm():
                    eng = engine(folder, effects)
                    go.wait()
                    results.append(("confirm", eng.confirm("alice", token)))
                    eng.close()

                def correct():
                    eng = engine(folder, effects)
                    go.wait()
                    results.append(("correct", eng.correct("siya", token, "amount", 2500000)))
                    eng.close()

                threads = [threading.Thread(target=confirm) for _ in range(12)] + [threading.Thread(target=correct)]
                for t in threads:
                    t.start()
                go.set()
                for t in threads:
                    t.join(60)
                paid = open(effects, encoding="utf-8").read().count("paid") if os.path.exists(effects) else 0
                ran_old = [r for k, r in results if k == "confirm" and r.get("ok")]
                corrected = [r for k, r in results if k == "correct" and r.get("ok")]
                self.assertLessEqual(paid, 1, (round_, results))
                self.assertEqual(len(ran_old), paid, (round_, results))
                check = engine(folder)
                try:
                    executing = [e["payload"] for e in check.audit.events() if e["event_type"] == "EXECUTING"]
                    self.assertEqual(len(executing), paid)
                    if paid:                                                                 # the OLD, heard items ran, never the new
                        self.assertEqual((executing[0]["token"], executing[0]["items_hash"]), (token, old_hash))
                    if corrected:
                        new = corrected[0]["token"]
                        self.assertEqual(check.confirm("alice", new)["error"], "unheard")   # the new items never run unheard
                    self.assertTrue(check.audit.verify()["ok"])
                    self.assertEqual(check.unheard_runs(), [])
                finally:
                    check.close()


if __name__ == "__main__":
    unittest.main()
