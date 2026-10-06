"""No error message, return value or log line written by Oathline contains a secret argument in clear.
(What an executor itself returns or raises is the executor's: that limit is stated in the README.)"""
import hashlib
import hmac
import json
import os
import tempfile
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore

SECRET = "sk-test-NOT-REAL-9f8e7d6c5b4a"  # gitleaks:allow
KEY = b"key-for-tests"


def proof(action):
    msg = f"{action['token']}|{action['capability']}|{action['arguments_hash']}".encode("utf-8")
    return hmac.new(KEY, msg, hashlib.sha256).hexdigest()


def check(principal, given, action):
    return principal == "alice" and isinstance(given, str) and hmac.compare_digest(given, proof(action))


class NoSecretLeakTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = os.path.join(self.tmp.name, "audit.db")
        reg = Registry([
            Capability("vault.read", frozenset({"agent", "alice"}), secret_arguments=frozenset({"api_key"})),
            Capability("vault.write", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True,
                       approvers=frozenset({"alice"}), secret_arguments=frozenset({"api_key"})),
            Capability("vault.none", frozenset({"agent"}), secret_arguments=frozenset({"api_key"})),
        ])
        self.clock = [1_000_000.0]
        self.eng = Engine(reg, audit=AuditLog(self.audit_path),
                          tokens=TokenStore(ttl_seconds=60, clock=lambda: self.clock[0]), approver_check=check)
        self.eng.register("vault.read", lambda a: {"length": len(a["api_key"])})
        self.eng.register("vault.write", lambda a: {"stored": True})
        self.seen = []

    def tearDown(self):
        self.eng.audit.close()
        self.eng.tokens.close()
        self.tmp.cleanup()

    def call(self, fn, *args, **kwargs):
        try:
            out = fn(*args, **kwargs)
            self.seen.append(json.dumps(out, default=repr))
            return out
        except Exception as e:  # noqa: BLE001 - an escaping error message is checked too
            self.seen.append(f"{type(e).__name__}: {e}")
            return {}

    def test_every_path_with_a_secret_argument(self):
        eng, args = self.eng, {"api_key": SECRET, "name": "db"}
        self.call(eng.request, "agent", "vault.read", args)                           # runs
        self.call(eng.request, "mallory", "vault.read", args)                         # denied
        self.call(eng.request, "agent", "vault.none", args)                           # no executor
        self.call(eng.request, "agent", "vault.destroy", args)                        # undeclared capability
        self.call(eng.request, " agent", "vault.read", args)                          # bad principal
        self.call(eng.request, "agent", "vault.read\x00", args)                       # bad capability
        self.call(eng.request, "agent", "vault.read", {"api_key": SECRET, "big": "x" * 20_000})     # too large
        self.call(eng.request, "agent", "vault.read", {"api_key": SECRET, "bad": {1, 2}})           # not storable
        self.call(eng.request, "agent", "vault.read", [SECRET])                       # not an object
        self.call(eng.propose, "agent", "vault.read", args)                           # not gated
        first = self.call(eng.request, "agent", "vault.write", args)
        second = self.call(eng.propose, "agent", "vault.write", args)
        third = self.call(eng.request, "alice", "vault.write", args)
        tok = first["token"]
        self.call(eng.action, tok)
        self.call(eng.confirm, "agent", tok)                                          # non-human
        self.call(eng.confirm, "alice", tok)                                          # no proof
        self.call(eng.confirm, "alice", tok, "a-guess")                               # wrong proof
        self.call(eng.confirm, "bob", tok, proof(eng.action(tok)))                    # not an approver
        self.call(eng.confirm, "alice", SECRET, "p")                                  # a secret used as a token
        self.call(eng.confirm, "alice", tok, proof(eng.action(tok)))                  # runs
        self.call(eng.confirm, "alice", tok, proof(eng.action(tok)))                  # already used
        eng.tokens._db.execute("UPDATE proposals SET arguments=? WHERE id=?",
                               (json.dumps({"api_key": SECRET, "name": "other"}), second["token"]))
        self.call(eng.confirm, "alice", second["token"], proof(eng.action(second["token"])))        # tampered
        self.clock[0] += 61
        self.call(eng.confirm, "alice", third["token"], proof(eng.action(third["token"])))          # expired
        self.call(eng.unfinished)
        self.call(eng.mismatched_runs)

        for text in self.seen:
            self.assertNotIn(SECRET, text)
        events = eng.audit.events()
        self.assertNotIn(SECRET, json.dumps(events))
        types = {e["event_type"] for e in events}
        for needed in ("REQUEST", "REQUEST_REFUSED", "DENIED", "EXECUTE_REFUSED", "EXECUTING", "EXECUTED",
                       "PROPOSED_BY_AGENT", "PROPOSED", "CONFIRM_REFUSED", "CONFIRMED_BY_HUMAN"):
            self.assertIn(needed, types)                                              # the paths were really walked
        self.assertTrue(eng.audit.verify()["ok"])
        eng.audit._db.execute("PRAGMA wal_checkpoint")
        with open(self.audit_path, "rb") as fh:
            self.assertNotIn(SECRET.encode("utf-8"), fh.read())                       # the audit file itself

    def test_a_secret_in_a_place_no_capability_declared_is_still_not_logged_by_a_refusal(self):
        eng = self.eng
        self.call(eng.request, SECRET + " ", "vault.read", {})                        # in a bad principal
        self.call(eng.request, "agent", SECRET + "\x00", {})                          # in a bad capability name
        self.call(eng.confirm, "alice", SECRET * 5, "p")                              # in a bad token
        self.call(eng.confirm, "alice", SECRET, "p")                                  # in something that is not a token id
        self.call(eng.confirm, SECRET + "​", "tok_" + "0" * 32, "p")             # in a bad confirmer name
        self.assertNotIn(SECRET, json.dumps(eng.audit.events()))
        for text in self.seen:
            self.assertNotIn(SECRET, text)


if __name__ == "__main__":
    unittest.main()
