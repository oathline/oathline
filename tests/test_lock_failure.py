"""The audit log's file is locked by another writer: nothing raises, nothing runs twice, and a human's
confirmation is not lost. These are the failures the v0.1.1 tag run showed on a slow Windows runner."""
import os
import random
import sqlite3
import tempfile
import threading
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore
from oathline import audit as audit_module


def registry():
    return Registry([Capability("pay.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True),
                     Capability("files.read", frozenset({"agent", "alice"}))])


class LockedAuditTest(unittest.TestCase):
    """Another connection holds the audit file's write lock for the whole call, and the busy wait is short."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.folder.name, "audit.db")
        self.tokens_path = os.path.join(self.folder.name, "tokens.db")
        self.old_wait = audit_module.BUSY_TIMEOUT_SECONDS
        audit_module.BUSY_TIMEOUT_SECONDS = 0.2
        self.ran = []
        self.holder = None
        self.eng = self.engine()

    def tearDown(self):
        self.release_lock()
        self.eng.close()
        audit_module.BUSY_TIMEOUT_SECONDS = self.old_wait
        self.folder.cleanup()

    def engine(self, tokens=None):
        eng = Engine(registry(), audit=AuditLog(self.audit_path), tokens=tokens or TokenStore(self.tokens_path))
        eng.register("pay.send", lambda a: self.ran.append(a["_principal"]) or {"paid": True})
        eng.register("files.read", lambda a: {"text": "hello"})
        return eng

    def hold_lock(self):
        self.holder = sqlite3.connect(self.audit_path, isolation_level=None, timeout=1)
        self.holder.execute("BEGIN IMMEDIATE")                   # the write lock, as another writer would hold it

    def release_lock(self):
        if self.holder is not None:
            self.holder.execute("ROLLBACK")
            self.holder.close()
            self.holder = None

    def types(self):
        return [e["event_type"] for e in self.eng.audit.events()]

    def test_a_confirmation_the_log_cannot_record_is_not_lost_and_the_action_runs_once_later(self):
        for proposer, confirmer, event in (("alice", "alice", "CONFIRMED"), ("agent", "alice", "CONFIRMED_BY_HUMAN")):
            with self.subTest(proposer=proposer):
                runs_before = self.types().count("EXECUTING")
                token = self.eng.request(proposer, "pay.send", {"amount": "10"})["token"]
                self.hold_lock()
                out = self.eng.confirm(confirmer, token)             # 0.1.1 raised sqlite3.OperationalError here
                self.release_lock()
                self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
                self.assertNotIn("warning", out)                     # the token was given back
                self.assertEqual(self.ran, [])                       # nothing ran
                self.assertEqual(self.eng.tokens.get(token)["state"], "proposed")
                self.assertNotIn(event, self.types())                # and nothing says it was confirmed
                again = self.eng.confirm(confirmer, token)           # the same confirmation, once the log is free
                self.assertEqual((again["ok"], again["state"]), (True, "executed"))
                self.assertEqual(self.ran, [proposer])
                self.assertEqual(self.eng.confirm(confirmer, token)["error"], "already_used")
                self.assertEqual(self.types().count("EXECUTING"), runs_before + 1)
                self.assertEqual(self.types().count(event), 1)
                self.assertEqual(self.eng.mismatched_runs(), [])
                self.assertTrue(self.eng.audit.verify()["ok"])
                self.ran.clear()

    def test_a_refusal_the_log_cannot_record_is_still_returned_and_nothing_raises(self):
        token = self.eng.request("alice", "pay.send", {"amount": "10"})["token"]
        self.assertTrue(self.eng.confirm("alice", token)["ok"])
        self.hold_lock()
        try:
            out = self.eng.confirm("alice", token)                               # a used token
            self.assertEqual((out["ok"], out["error"], out["warning"]), (False, "already_used", "refusal_not_recorded"))
            out = self.eng.confirm("agent", token)                               # a non-human name
            self.assertEqual((out["ok"], out["error"], out["warning"]), (False, "non_human_principal", "refusal_not_recorded"))
            out = self.eng.request(" alice", "pay.send")                          # a bad name
            self.assertEqual((out["ok"], out["error"], out["warning"]), (False, "bad_principal", "refusal_not_recorded"))
            out = self.eng.request("alice", "files.read")                        # a read: no record, no run
            self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
            out = self.eng.request("alice", "pay.send", {"amount": "20"})         # a proposal: no record, no token
            self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
            self.assertNotIn("token", out)
            out = self.eng.propose("agent", "pay.send", {"amount": "30"})
            self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
            self.assertNotIn("token", out)
        finally:
            self.release_lock()
        rows = self.eng.tokens._db.execute("SELECT state FROM proposals ORDER BY created").fetchall()
        self.assertEqual([r[0] for r in rows], ["used"])                         # nothing was stored for them
        self.assertEqual(self.ran, ["alice"])
        self.assertTrue(self.eng.audit.verify()["ok"])
        self.assertEqual(self.types().count("EXECUTED"), 1)

    def test_a_proposal_whose_record_cannot_be_written_is_withdrawn(self):
        class LogThatDropsProposals(AuditLog):
            def append(self, event_type, principal, action, payload=None):
                if event_type in ("PROPOSED", "PROPOSED_BY_AGENT"):
                    raise sqlite3.OperationalError("database is locked")
                return super().append(event_type, principal, action, payload)

        eng = Engine(registry(), audit=LogThatDropsProposals(), tokens=TokenStore(self.tokens_path))
        eng.register("pay.send", lambda a: self.ran.append(a["_principal"]) or {"paid": True})
        try:
            for call in (lambda: eng.request("alice", "pay.send", {"amount": "10"}),
                         lambda: eng.propose("agent", "pay.send", {"amount": "10"})):
                out = call()
                self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
                self.assertNotIn("token", out)
            rows = eng.tokens._db.execute("SELECT id, state FROM proposals").fetchall()
            self.assertEqual([state for _, state in rows], ["expired", "expired"])   # stored, then withdrawn
            for token, _ in rows:
                self.assertEqual(eng.confirm("alice", token)["error"], "expired")   # it can never run
            self.assertEqual(self.ran, [])
            self.assertEqual([e for e in eng.audit.events() if e["event_type"].startswith("PROPOSED")], [])
        finally:
            eng.close()

    def test_when_the_token_cannot_be_given_back_either_the_caller_is_told_it_is_spent(self):
        class StuckStore(TokenStore):
            def release(self, token, confirmer):
                raise sqlite3.OperationalError("database is locked")

        eng = self.engine(tokens=StuckStore(self.tokens_path))
        try:
            token = eng.request("alice", "pay.send", {"amount": "10"})["token"]
            self.hold_lock()
            out = eng.confirm("alice", token)
            self.release_lock()
            self.assertEqual((out["ok"], out["state"], out["error"], out["warning"]),
                             (False, "not_run", "audit_write_failed", "token_spent"))
            self.assertEqual(self.ran, [])
            self.assertEqual(eng.confirm("alice", token)["error"], "already_used")
        finally:
            eng.close()


class FlakyLogRaceTest(unittest.TestCase):
    def test_twenty_confirmers_on_a_flaky_log_run_the_action_once_and_never_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            effects = os.path.join(folder, "effects.txt")
            rng, coin = random.Random(20261008), threading.Lock()

            class FlakyLog(AuditLog):
                """A log whose writes fail three times in ten, as a locked file does on a slow disk."""

                def append(self, *a, **k):
                    with coin:
                        fail = rng.random() < 0.3
                    if fail:
                        raise sqlite3.OperationalError("database is locked")
                    return super().append(*a, **k)

            def engine():
                eng = Engine(registry(), audit=FlakyLog(os.path.join(folder, "audit.db")),
                             tokens=TokenStore(os.path.join(folder, "tokens.db")))

                def pay(a):
                    with open(effects, "a", encoding="utf-8") as fh:
                        fh.write("paid\n")
                    return {"paid": True}

                eng.register("pay.send", pay)
                eng.register("files.read", lambda a: {})
                return eng

            first = Engine(registry(), audit=AuditLog(os.path.join(folder, "audit.db")),
                           tokens=TokenStore(os.path.join(folder, "tokens.db")))
            token = first.request("agent", "pay.send", {"amount": "10"})["token"]
            first.close()
            results, errors = [], []
            start = threading.Barrier(20)

            def worker():
                try:
                    eng = engine()
                    start.wait()
                    for _ in range(50):                              # a caller tries again while told nothing ran
                        out = eng.confirm("alice", token)
                        if not (out.get("error") == "audit_write_failed" and out.get("state") == "not_run"):
                            break
                    results.append(out)
                    eng.close()
                except Exception as e:  # noqa: BLE001
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 20)
            winners = [r for r in results if r["ok"] or r.get("state") == "ran_unrecorded"]
            self.assertEqual(len(winners), 1, results)
            with open(effects, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "paid\n")
            with AuditLog(os.path.join(folder, "audit.db")) as log:
                self.assertTrue(log.verify()["ok"])
                self.assertEqual(len(log.events("EXECUTING")), 1)
            self.assertTrue({r["error"] for r in results if not r["ok"]} <= {"already_used", "ran_but_not_recorded"})


if __name__ == "__main__":
    unittest.main()
