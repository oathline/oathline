"""Every place that takes a name, or a list of names, takes plain `str` only.
Anything else is refused with an error at once. It is never accepted and then ignored."""
import enum
import unittest

from oathline import (AuditError, AuditLog, Capability, ConfigError, Denied, Engine, Registry, TokenStore,
                      is_non_human, validate)


class Role(str, enum.Enum):
    DEPLOY = "deploy-bot"


class Text(str):
    pass


class Liar(str):
    """Claims to be equal to anything, and hashes like "alice"."""

    def __eq__(self, other):
        return True

    def __hash__(self):
        return hash("alice")


NOT_PLAIN_TEXT = [Role.DEPLOY, Text("deploy-bot"), b"deploy-bot", None, ["deploy-bot"]]


def registry():
    return Registry([Capability("site.deploy", frozenset({"deploy-bot", "alice"}), writes=True, needs_confirmation=True),
                     Capability("site.read", frozenset({"alice"}))])


class NameListsTest(unittest.TestCase):
    def test_non_human_list_refuses_anything_that_is_not_plain_text(self):
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(TypeError, msg=repr(bad)):
                Engine(registry(), non_human=["agent", bad])
            with self.assertRaises(TypeError, msg=repr(bad)):
                Engine(registry(), non_human=[bad])
        for bad_list in (None, "agent", b"agent", 5, Role.DEPLOY):
            with self.assertRaises(TypeError, msg=repr(bad_list)):
                Engine(registry(), non_human=bad_list)

    def test_the_enum_case_that_used_to_be_accepted_and_ignored(self):
        with self.assertRaises(TypeError) as raised:
            Engine(registry(), non_human=frozenset({Role.DEPLOY}))
        self.assertIn("non_human", str(raised.exception))
        eng = Engine(registry(), non_human=frozenset({Role.DEPLOY.value}))      # the plain text is the way to do it
        ran = []
        eng.register("site.deploy", lambda a: ran.append(1) or {"ok": True})
        tok = eng.request("deploy-bot", "site.deploy", {})["token"]
        self.assertEqual(eng.confirm("deploy-bot", tok)["error"], "non_human_principal")
        self.assertEqual(ran, [])

    def test_humans_list_refuses_anything_that_is_not_plain_text(self):
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(TypeError, msg=repr(bad)):
                Engine(registry(), humans=["alice", bad])
        for bad_list in ("alice", b"alice", 5, Role.DEPLOY):
            with self.assertRaises(TypeError, msg=repr(bad_list)):
                Engine(registry(), humans=bad_list)

    def test_capability_name_sets_refuse_anything_that_is_not_plain_text(self):
        for bad in NOT_PLAIN_TEXT:
            for field in ("principals", "approvers", "secret_arguments"):
                kwargs = {"principals": frozenset({"alice"}), field: ["alice", bad]}
                with self.assertRaises(TypeError, msg=(field, repr(bad))):
                    Capability("x.do", **kwargs)
            with self.assertRaises((TypeError, ValueError), msg=repr(bad)):
                Registry([Capability(bad, frozenset({"alice"}))])

    def test_a_container_that_changes_between_two_readings_is_read_once(self):
        class Twice(list):
            """A list that shows one thing the first time it is walked and another the second time."""

            def __init__(self, first, second):
                super().__init__(first)
                self.shown = [first, second]

            def __iter__(self):
                return iter(self.shown.pop(0) if len(self.shown) > 1 else self.shown[0])

        cap = Capability("x.w", Twice(["alice"], ["mallory"]), writes=True, needs_confirmation=True)
        self.assertEqual(cap.principals, frozenset({"alice"}))                     # what was checked is what is kept
        with self.assertRaises(TypeError):
            Capability("x.w", frozenset({"alice"}), secret_arguments=Twice([Role.DEPLOY], ["key"]))

    def test_is_non_human_refuses_a_list_it_cannot_use(self):
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(TypeError, msg=repr(bad)):
                is_non_human("deploy-bot", ["agent", bad])
        for bad_list in (None, "agent", b"agent", 5):
            with self.assertRaises(TypeError, msg=repr(bad_list)):
                is_non_human("deploy-bot", bad_list)

    def test_a_capability_declares_its_argument_names_and_a_mistake_shows_when_it_is_built(self):
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(TypeError, msg=repr(bad)):
                Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True, arguments=["to", bad])
        with self.assertRaises(TypeError):
            Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True, arguments="to")
        reg = Registry([Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True,
                                   arguments=frozenset({"to"}))])
        v = validate({"intent": "email.send", "arguments": {"to": "bob@example.com", "cc": "bob@example.com"}},
                     "mail bob@example.com", reg)
        self.assertEqual(v.arguments, {"to": "bob@example.com"})             # declared on the capability: kept
        self.assertIn("argument_not_declared:cc", v.violations)

    def test_allowed_arguments_refuses_anything_that_is_not_plain_text(self):
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(TypeError, msg=repr(bad)):
                validate({"intent": "site.read"}, "text", registry(), allowed_arguments=("to", bad))
        for bad_list in ("to", b"to", None, 5):
            with self.assertRaises(TypeError, msg=repr(bad_list)):
                validate({"intent": "site.read"}, "text", registry(), allowed_arguments=bad_list)

    def test_registry_lookups_never_match_something_that_is_not_plain_text(self):
        reg = registry()
        for bad in NOT_PLAIN_TEXT + [Liar("mallory"), Liar("site.read")]:
            with self.assertRaises(Denied, msg=repr(bad)):
                reg.authorize(bad, "site.read")
            with self.assertRaises(Denied, msg=repr(bad)):
                reg.authorize("alice", bad)
            self.assertIsNone(reg.get(bad), repr(bad))
            self.assertFalse(bad in reg, repr(bad))
        self.assertEqual(reg.authorize("alice", "site.read").capability, "site.read")

    def test_register_refuses_a_capability_name_that_is_not_plain_text(self):
        eng = Engine(registry())
        for bad in NOT_PLAIN_TEXT + [Liar("site.read")]:
            with self.assertRaises((TypeError, ValueError), msg=repr(bad)):
                eng.register(bad, lambda a: {})
        eng.register("site.read", lambda a: {})

    def test_token_store_refuses_names_that_are_not_plain_text(self):
        store = TokenStore()
        for bad in NOT_PLAIN_TEXT:
            with self.assertRaises(ConfigError, msg=repr(bad)):
                store.propose(bad, "site.deploy", {})
            with self.assertRaises(ConfigError, msg=repr(bad)):
                store.propose("alice", bad, {})
        tok = store.propose("alice", "site.deploy", {})
        for bad in NOT_PLAIN_TEXT + [Liar("mallory")]:
            self.assertFalse(store.confirm(bad, tok).ok, repr(bad))
            self.assertFalse(store.redeem(tok, confirmer=bad, authorise=lambda proposer, cap: None).ok, repr(bad))
            self.assertFalse(store.confirm("alice", bad).ok, repr(bad))
        self.assertEqual(store.get(tok)["state"], "proposed")
        self.assertTrue(store.confirm("alice", tok).ok)

    def test_audit_log_refuses_names_that_are_not_plain_text(self):
        log = AuditLog()
        for bad in NOT_PLAIN_TEXT:
            for args in ((bad, "alice", "x.do"), ("EVENT", bad, "x.do"), ("EVENT", "alice", bad)):
                with self.assertRaises(AuditError, msg=repr(bad)):
                    log.append(*args)
        self.assertEqual(log.events(), [])

    def test_engine_calls_refuse_names_that_are_not_plain_text(self):
        eng = Engine(registry())
        ran = []
        eng.register("site.deploy", lambda a: ran.append(1) or {"ok": True})
        eng.register("site.read", lambda a: ran.append(1) or {"ok": True})
        tok = eng.request("alice", "site.deploy", {})["token"]
        for bad in NOT_PLAIN_TEXT + [Liar("mallory"), Liar("alice")]:
            self.assertFalse(eng.request(bad, "site.read")["ok"], repr(bad))
            self.assertFalse(eng.request("alice", bad)["ok"], repr(bad))
            self.assertFalse(eng.propose(bad, "site.deploy")["ok"], repr(bad))
            self.assertFalse(eng.confirm(bad, tok)["ok"], repr(bad))
            self.assertFalse(eng.confirm("alice", bad)["ok"], repr(bad))
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
