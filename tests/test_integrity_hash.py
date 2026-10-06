"""The integrity hash: the same arguments give the same hash, and arguments that cannot be stored
are refused before a proposal exists."""
import unittest

from oathline import Capability, ConfigError, Engine, Registry, TokenStore


def make():
    reg = Registry([Capability("pay.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg)
    ran = []
    eng.register("pay.send", lambda a: ran.append({k: v for k, v in a.items() if not k.startswith("_")}) or {"paid": 1})
    return eng, ran


def hash_of(eng, arguments):
    out = eng.request("agent", "pay.send", arguments)
    assert out.get("state") == "proposed", out
    return eng.action(out["token"])["arguments_hash"]


class IntegrityHashTest(unittest.TestCase):
    def test_key_order_does_not_change_the_hash(self):
        eng, _ = make()
        a = hash_of(eng, {"to": "bob", "amount": "10", "note": {"x": 1, "y": 2}})
        b = hash_of(eng, {"note": {"y": 2, "x": 1}, "amount": "10", "to": "bob"})
        self.assertEqual(a, b)

    def test_the_same_arguments_give_the_same_hash_every_time(self):
        eng, _ = make()
        args = {"amount": 0.1 + 0.2, "tiny": 1e-300, "big": 1e300, "neg": -0.0, "n": 10 ** 30, "t": [1, "a", None, True]}
        self.assertEqual(len({hash_of(eng, dict(args)) for _ in range(5)}), 1)
        other = TokenStore()
        tok = other.propose("agent", "pay.send", dict(args))
        self.assertEqual(other.get(tok)["integrity"], hash_of(eng, args))            # a second store agrees

    def test_different_values_give_different_hashes(self):
        eng, _ = make()
        hashes = [hash_of(eng, {"v": v}) for v in (True, 1, 1.0, "1", "true", None, 0, False, [1], {"1": 1}, "")]
        self.assertEqual(len(set(hashes)), len(hashes))                              # True is not 1 is not 1.0 is not "1"

    def test_number_keys_are_not_a_false_tamper_alarm(self):
        for arguments in ({1: "x", 10: "y", 2: "z"}, {"ids": {1: "a", 10: "b", 2: "c"}}, {2.5: 1, 10: 2}):
            eng, ran = make()
            tok = eng.request("agent", "pay.send", arguments)["token"]
            out = eng.confirm("alice", tok)
            self.assertTrue(out["ok"], (arguments, out))
            self.assertNotIn("tampered", [e["payload"].get("reason") for e in eng.audit.events()])
            self.assertEqual(len(ran), 1)
            self.assertTrue(all(isinstance(k, str) for k in ran[0]))                 # stored and run as JSON reads them
        store = TokenStore()
        tok = store.propose("alice", "pay.send", {1: "x", 10: "y", 2: "z"})
        c = store.confirm("alice", tok)
        self.assertTrue(c.ok)
        self.assertEqual(c.arguments, {"1": "x", "10": "y", "2": "z"})

    def test_what_runs_is_what_was_hashed(self):
        eng, ran = make()
        tok = eng.request("agent", "pay.send", {"amount": 10, "to": ("bob", "carol")})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(ran[0], {"amount": 10, "to": ["bob", "carol"]})
        self.assertEqual(eng.tokens.get(tok)["arguments"], ran[0])

    def test_arguments_that_cannot_be_stored_are_refused_before_a_proposal_exists(self):
        class Thing:
            pass
        cyc = {}
        cyc["self"] = cyc
        for arguments in ({"v": float("nan")}, {"v": float("inf")}, {"v": -float("inf")}, {"v": b"bytes"},
                          {"v": {1, 2}}, {"v": Thing()}, {"v": "\ud800"}, {1: "a", "1": "b"}, {("a", "b"): 1}, cyc,
                          {"v": 2 + 3j}):
            eng, ran = make()
            for call in (eng.request, eng.propose):
                out = call("agent", "pay.send", arguments)
                self.assertEqual((out["ok"], out["error"]), (False, "arguments_not_storable"), repr(arguments)[:40])
            self.assertEqual(eng.tokens._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)
            self.assertEqual({e["event_type"] for e in eng.audit.events()}, {"REQUEST_REFUSED"})
            self.assertEqual(ran, [])

    def test_the_token_store_itself_refuses_what_it_cannot_store(self):
        store = TokenStore()
        for arguments in ({"v": float("nan")}, {"v": b"x"}, {"v": {1}}, {1: "a", "1": "b"}, [1], "text", None):
            with self.assertRaises(ConfigError, msg=repr(arguments)):
                store.propose("alice", "pay.send", arguments)
        self.assertEqual(store._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)

    def test_the_hash_is_in_the_log_at_proposal_and_at_execution(self):
        eng, _ = make()
        tok = eng.request("agent", "pay.send", {"amount": "10"})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        h = eng.action(tok)["arguments_hash"]
        proposed = eng.audit.events("PROPOSED_BY_AGENT")[0]["payload"]
        executing = eng.audit.events("EXECUTING")[0]["payload"]
        self.assertEqual((proposed["arguments_hash"], executing["arguments_hash"]), (h, h))

    def test_a_rewritten_proposal_shows_in_the_log_as_two_different_hashes(self):
        """The stated limit: someone who can write the token store can change what runs.
        The log then holds the hash of what was proposed and a different hash for what ran."""
        import hashlib
        import json
        eng, ran = make()
        tok = eng.request("agent", "pay.send", {"amount": "10"})["token"]
        evil = {"amount": "9999"}
        blob = json.dumps(["agent", "pay.send", evil], sort_keys=True, separators=(",", ":"))
        eng.tokens._db.execute("UPDATE proposals SET arguments=?, integrity=? WHERE id=?",
                               (json.dumps(evil), hashlib.sha256(blob.encode("utf-8")).hexdigest(), tok))
        self.assertTrue(eng.confirm("alice", tok)["ok"])                             # it runs: this is the limit
        self.assertEqual(ran[0], evil)
        proposed = eng.audit.events("PROPOSED_BY_AGENT")[0]["payload"]["arguments_hash"]
        executing = eng.audit.events("EXECUTING")[0]["payload"]["arguments_hash"]
        self.assertNotEqual(proposed, executing)
        self.assertEqual(eng.mismatched_runs(), [{"token": tok, "reason": "hash_differs", "proposed_hash": proposed,
                                                  "ran_hash": executing}])
        self.assertTrue(eng.audit.verify()["ok"])


if __name__ == "__main__":
    unittest.main()
