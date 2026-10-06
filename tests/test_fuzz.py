"""Fuzz: 20,000 seeded random structures into validate(), request(), propose() and confirm().
No exception escapes, no write runs without a valid human confirmation, the chain verifies after each batch."""
import random
import unittest

from oathline import Capability, Engine, Registry, Validated, validate

SEED = 20261006
BATCHES, PER_BATCH = 20, 1000                     # 20,000 structures in all
NAMES = ["agent", "alice", "bob", "model", "mallory", "Agent", " alice", "alice​", "аlice", "", "a" * 200,
         "notes.read", "email.send", "files.delete", "bank.transfer", "unknown"]
ATOMS = [None, True, False, 0, 1, -1, 2.5, 10 ** 400, float("nan"), float("inf"), "", "x", "approve", "\ud800",
         "\x00", "​", b"bytes", "tok_" + "0" * 32]


def junk(rng, depth=0):
    kind = rng.randrange(12)
    if depth > 3 or kind < 5:
        return rng.choice(ATOMS + NAMES)
    if kind == 5:
        return rng.random() * 10 ** rng.randrange(-5, 30)
    if kind == 6:
        return "".join(chr(rng.choice((rng.randrange(32, 127), rng.randrange(0, 0x2FFF)))) for _ in range(rng.randrange(0, 12)))
    if kind == 7:
        return [junk(rng, depth + 1) for _ in range(rng.randrange(0, 4))]
    if kind == 8:
        return tuple(junk(rng, depth + 1) for _ in range(rng.randrange(0, 3)))
    if kind == 9:
        return {rng.choice(NAMES + [1, 2.5, None, True, "approve", "arguments", "candidates", "intent", "confidence"]):
                junk(rng, depth + 1) for _ in range(rng.randrange(0, 4))}
    if kind == 10:
        return {"intent": junk(rng, depth + 1), "candidates": junk(rng, depth + 1), "arguments": junk(rng, depth + 1),
                "confidence": junk(rng, depth + 1), rng.choice(["approve", "permissions", "x"]): junk(rng, depth + 1)}
    return object()


def make():
    reg = Registry([Capability("notes.read", frozenset({"agent", "alice", "bob"})),
                    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True,
                               approvers=frozenset({"alice"}), secret_arguments=frozenset({"key"})),
                    Capability("files.delete", frozenset({"agent", "bob"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg)
    runs = []
    eng.register("notes.read", lambda a: {"ok": True})
    eng.register("email.send", lambda a: runs.append(("email.send", a.get("_principal"), a.get("_confirmed_by"))) or {"s": 1})
    eng.register("files.delete", lambda a: runs.append(("files.delete", a.get("_principal"), a.get("_confirmed_by"))) or {"d": 1})
    return eng, reg, runs


class FuzzTest(unittest.TestCase):
    def test_twenty_thousand_random_structures(self):
        rng = random.Random(SEED)
        eng, reg, runs = make()
        live_tokens = []
        total = 0
        for batch in range(BATCHES):
            for _ in range(PER_BATCH):
                total += 1
                which = rng.randrange(4)
                a, b, c = junk(rng), junk(rng), junk(rng)
                if which == 0:
                    v = validate(a, b, reg, allowed_arguments=("to", "key", "x"))
                    self.assertIsInstance(v, Validated)
                    self.assertTrue(set(v.candidates) <= set(reg.names()))
                    self.assertTrue(all(isinstance(k, str) and isinstance(s, str) for k, s in v.arguments.items()))
                    self.assertTrue(0.0 <= v.confidence <= 1.0)
                elif which == 1:
                    out = eng.request(a, rng.choice([b, "notes.read", "email.send", "files.delete"]), c)
                    self.assertIsInstance(out, dict)
                    if out.get("state") == "proposed":
                        live_tokens.append(out["token"])
                elif which == 2:
                    out = eng.propose(rng.choice([a, "agent", "alice"]), rng.choice([b, "email.send", "files.delete"]),
                                      rng.choice([c, {}, {"to": "x"}]))
                    self.assertIsInstance(out, dict)
                    if out.get("state") == "proposed":
                        live_tokens.append(out["token"])
                else:
                    token = rng.choice(live_tokens) if live_tokens and rng.random() < 0.7 else b
                    out = eng.confirm(rng.choice([a, "agent", "alice", "bob", "mallory", "model"]), token, rng.choice([None, c]))
                    self.assertIsInstance(out, dict)
                self.assertIn("ok", out) if which else None
            check = eng.audit.verify(expected_head=eng.audit.head())
            self.assertTrue(check["ok"], (batch, check))
            self.check_every_write_had_a_human(eng, runs)
        self.assertEqual(total, 20_000)
        self.assertGreater(len(runs), 20)                 # the fuzz did reach real confirmed writes
        self.assertGreater(len(eng.audit.events("REQUEST_REFUSED")), 100)

    def check_every_write_had_a_human(self, eng, runs):
        """Every write that ran was confirmed, by a name that may confirm it, exactly once per token."""
        events = eng.audit.events()
        confirmed = [e for e in events if e["event_type"] in ("CONFIRMED", "CONFIRMED_BY_HUMAN")]
        executed = [e for e in events if e["event_type"] == "EXECUTED" and e["action"] in ("email.send", "files.delete")]
        self.assertEqual(len(executed), len(runs))
        self.assertEqual(len(executed), len(confirmed))
        tokens = [e["payload"]["token"] for e in confirmed]
        self.assertEqual(len(tokens), len(set(tokens)))                        # no token confirmed twice
        for e in confirmed:
            self.assertIn(e["principal"], ("alice", "bob"))                    # never an agent, a model or junk
            if e["action"] == "email.send":
                self.assertEqual(e["principal"], "alice")
        for action, principal, confirmed_by in runs:
            self.assertIn(principal, ("agent", "alice", "bob"))
            if principal == "agent":
                self.assertIn(confirmed_by, ("alice", "bob"))
                if action == "email.send":
                    self.assertEqual(confirmed_by, "alice")
                else:
                    self.assertEqual(confirmed_by, "bob")

    def test_the_fuzz_is_repeatable(self):
        first = [repr(junk(random.Random(SEED + i)))[:60] for i in range(50)]
        again = [repr(junk(random.Random(SEED + i)))[:60] for i in range(50)]
        self.assertEqual([x for x in first if "object at" not in x], [x for x in again if "object at" not in x])


if __name__ == "__main__":
    unittest.main()
