"""Size and depth: huge or deeply nested input is refused in bounded time, and nothing runs."""
import time
import unittest

from oathline import Capability, Engine, Registry, validate
from oathline.engine import ARGUMENTS_MAX_BYTES


def make():
    reg = Registry([Capability("notes.read", frozenset({"agent", "alice"})),
                    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg)
    ran = []
    eng.register("notes.read", lambda a: ran.append("read") or {"n": 1})
    eng.register("email.send", lambda a: ran.append("send") or {"n": 1})
    return eng, ran


def nested(depth):
    value = "leaf"
    for _ in range(depth):
        value = [value]
    return value


def nested_dict(depth):
    value = {"k": "leaf"}
    for _ in range(depth):
        value = {"k": value}
    return value


class LimitsTest(unittest.TestCase):
    def refused(self, arguments, error):
        eng, ran = make()
        started = time.perf_counter()
        for call, capability in ((eng.request, "notes.read"), (eng.request, "email.send"), (eng.propose, "email.send")):
            out = call("agent", capability, arguments)
            self.assertEqual((out["ok"], out["error"]), (False, error))
        self.assertLess(time.perf_counter() - started, 20)
        self.assertEqual(ran, [])
        self.assertEqual({e["event_type"] for e in eng.audit.events()}, {"REQUEST_REFUSED"})
        self.assertLess(max(len(str(e["payload"])) for e in eng.audit.events()), 200)     # the log stays small
        self.assertEqual(eng.tokens._db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0], 0)
        self.assertTrue(eng.audit.verify()["ok"])

    def test_five_megabytes_of_arguments(self):
        self.refused({"body": "x" * 5_000_000}, "arguments_too_large")
        self.refused({f"k{i}": "v" * 50 for i in range(80_000)}, "arguments_too_large")

    def test_just_over_and_just_under_the_limit(self):
        eng, ran = make()
        under = {"body": "x" * (ARGUMENTS_MAX_BYTES - len('{"body":""}'))}
        self.assertTrue(eng.request("agent", "notes.read", under)["ok"])
        self.assertEqual(eng.request("agent", "notes.read", {"body": under["body"] + "x"})["error"], "arguments_too_large")
        self.assertEqual(ran, ["read"])

    def test_ten_thousand_levels_of_nesting(self):
        self.refused({"deep": nested(10_000)}, "arguments_not_storable")
        self.refused(nested_dict(10_000), "arguments_not_storable")

    def test_the_depth_limit_exactly(self):
        eng, ran = make()
        self.assertTrue(eng.request("agent", "notes.read", {"deep": nested(31)})["ok"])          # 32 levels with the object
        self.assertEqual(eng.request("agent", "notes.read", {"deep": nested(32)})["error"], "arguments_not_storable")
        self.assertEqual(ran, ["read"])

    def test_arguments_that_are_not_an_object(self):
        for bad in ([1, 2], "text", 5, ("a",), {1, 2}, b"x"):
            self.refused(bad, "bad_arguments")

    def test_a_million_candidates(self):
        reg = Registry([Capability("notes.read", frozenset({"agent"}))])
        started = time.perf_counter()
        v = validate({"candidates": ["wire.money"] * 1_000_000}, "text", reg)
        w = validate({"candidates": [f"cap{i}" for i in range(1_000_000)] + ["notes.read"]}, "text", reg)
        self.assertLess(time.perf_counter() - started, 20)
        for result in (v, w):
            self.assertEqual(result.candidates, [])
            self.assertIn("too_many_candidates", result.violations)
            self.assertLess(len(result.violations), 200)

    def test_a_million_arguments_and_deep_model_output(self):
        reg = Registry([Capability("notes.read", frozenset({"agent"}))])
        started = time.perf_counter()
        v = validate({"intent": "notes.read", "arguments": {f"k{i}": "x" for i in range(1_000_000)}}, "x", reg,
                     allowed_arguments=("k1",))
        self.assertIn("too_many_arguments", v.violations)
        self.assertEqual(v.arguments, {})
        self.assertLess(len(v.violations), 200)
        deep = validate({"intent": nested(10_000), "candidates": nested(10_000), "arguments": nested_dict(10_000),
                         "confidence": nested(10_000)}, "x", reg)
        self.assertEqual((deep.candidates, deep.arguments, deep.confidence), ([], {}, 0.0))
        self.assertEqual(validate(nested_dict(10_000), "x", reg).candidates, [])
        self.assertLess(time.perf_counter() - started, 20)

    def test_a_huge_proof_or_token_is_refused_and_not_logged(self):
        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        self.assertEqual(eng.confirm("alice", "t" * 5_000_000)["error"], "unknown_token")
        self.assertEqual(eng.confirm("a" * 5_000_000, tok)["error"], "bad_principal")
        self.assertLess(max(len(str(e["payload"])) for e in eng.audit.events()), 300)
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
