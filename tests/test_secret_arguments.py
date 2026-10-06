"""Secret arguments: a hash marker in the audit log, the real value to the executor and in the token."""
import hashlib
import json
import unittest

from oathline import Capability, Engine, Registry
from oathline.audit import SCHEMA_VERSION

SECRET = "s3cr3t-value-for-tests"


def marker(value):
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "secret:sha256:" + hashlib.sha256(blob).hexdigest()[:12]


def make():
    reg = Registry([
        Capability("volume.delete", frozenset({"builder", "alice"}), writes=True, needs_confirmation=True,
                   secret_arguments=frozenset({"api_token"})),
        Capability("volume.read", frozenset({"builder"}), secret_arguments=frozenset({"api_token"})),
        Capability("notes.read", frozenset({"builder"})),
    ])
    eng = Engine(reg, non_human=frozenset({"builder"}))
    got = []
    eng.register("volume.delete", lambda a: got.append(dict(a)) or {"deleted": a["volume"]})
    eng.register("volume.read", lambda a: got.append(dict(a)) or {"volumes": 3})
    eng.register("notes.read", lambda a: {"notes": 1})
    return eng, got


def logged(eng):
    return json.dumps(eng.audit.events())


class SecretArgumentsTest(unittest.TestCase):
    def test_secret_is_a_hash_marker_in_the_audit_log_never_in_clear(self):
        eng, _ = make()
        eng.request("builder", "volume.read", {"api_token": SECRET})                          # a read that runs
        tok = eng.request("builder", "volume.delete", {"volume": "v1", "api_token": SECRET})["token"]
        tok2 = eng.propose("builder", "volume.delete", {"volume": "v2", "api_token": SECRET})["token"]
        eng.confirm("builder", tok)                                                           # refused
        eng.confirm("alice", tok2)                                                            # runs
        eng.request("mallory", "volume.delete", {"volume": "v3", "api_token": SECRET})        # denied
        self.assertNotIn(SECRET, logged(eng))
        requests = [e["payload"]["arguments"] for e in eng.audit.events() if e["event_type"] == "REQUEST"]
        self.assertEqual(len(requests), 4)
        for args in requests:
            self.assertEqual(args["api_token"], marker(SECRET))
        self.assertEqual(requests[1]["volume"], "v1")                # other arguments stay readable
        self.assertTrue(eng.audit.verify()["ok"])

    def test_executor_still_receives_the_real_value(self):
        eng, got = make()
        eng.request("builder", "volume.read", {"api_token": SECRET})
        tok = eng.propose("builder", "volume.delete", {"volume": "v1", "api_token": SECRET})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual([g["api_token"] for g in got], [SECRET, SECRET])

    def test_confirmation_token_still_binds_the_real_arguments(self):
        eng, got = make()
        tok = eng.propose("builder", "volume.delete", {"volume": "v1", "api_token": SECRET})["token"]
        self.assertEqual(eng.tokens.get(tok)["arguments"], {"volume": "v1", "api_token": SECRET})
        eng.tokens._db.execute("UPDATE proposals SET arguments=? WHERE id=?",
                               (json.dumps({"volume": "v1", "api_token": "another-secret"}), tok))
        self.assertEqual(eng.confirm("alice", tok)["error"], "tampered")
        self.assertEqual(got, [])

    def test_undeclared_capability_has_every_argument_value_masked(self):
        eng, _ = make()
        self.assertEqual(eng.request("builder", "volume.destroy", {"volume": "v1", "key": SECRET})["error"], "denied")
        self.assertEqual(eng.propose("builder", "volume.destroy", {"key": SECRET})["error"], "denied")
        self.assertNotIn(SECRET, logged(eng))
        first = eng.audit.events("REQUEST")[0]["payload"]["arguments"]
        self.assertEqual(first, {"volume": marker("v1"), "key": marker(SECRET)})

    def test_same_value_same_marker_different_value_different_marker(self):
        eng, _ = make()
        eng.request("builder", "volume.read", {"api_token": SECRET})
        eng.request("builder", "volume.read", {"api_token": SECRET})
        eng.request("builder", "volume.read", {"api_token": SECRET + "x"})
        a, b, c = (e["payload"]["arguments"]["api_token"] for e in eng.audit.events("REQUEST"))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), len("secret:sha256:") + 12)

    def test_capability_without_secret_arguments_logs_as_before(self):
        eng, _ = make()
        eng.request("builder", "notes.read", {"q": "plain"})
        self.assertEqual(eng.audit.events("REQUEST")[0]["payload"], {"arguments": {"q": "plain"}})

    def test_audit_format_is_unchanged(self):
        eng, _ = make()
        eng.request("builder", "volume.read", {"api_token": SECRET})
        self.assertEqual(SCHEMA_VERSION, 1)
        self.assertEqual({e["schema_version"] for e in eng.audit.events()}, {1})
        self.assertTrue(eng.audit.verify(expected_head=eng.audit.head())["ok"])


if __name__ == "__main__":
    unittest.main()
