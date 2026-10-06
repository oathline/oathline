import unittest

from oathline import Capability, Denied, Registry


def reg():
    return Registry([
        Capability("notes.read", frozenset({"agent", "alice"})),
        Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True),
        Capability("disabled.any", frozenset()),
    ])


class RegistryTest(unittest.TestCase):
    def test_granted_read(self):
        g = reg().authorize("agent", "notes.read")
        self.assertFalse(g.writes)
        self.assertFalse(g.needs_confirmation)

    def test_unknown_capability_denied(self):
        with self.assertRaises(Denied):
            reg().authorize("agent", "bank.transfer")

    def test_principal_not_granted(self):
        with self.assertRaises(Denied):
            reg().authorize("mallory", "notes.read")

    def test_empty_grant_denies_everyone(self):
        with self.assertRaises(Denied):
            reg().authorize("agent", "disabled.any")

    def test_writes_must_be_confirmed(self):
        with self.assertRaises(ValueError):
            Registry([Capability("db.write", frozenset({"agent"}), writes=True)])

    def test_duplicates_rejected(self):
        with self.assertRaises(ValueError):
            Registry([Capability("a", frozenset()), Capability("a", frozenset())])

    def test_registry_is_frozen(self):
        r = reg()
        with self.assertRaises(TypeError):
            r._caps["new.cap"] = Capability("new.cap", frozenset({"agent"}))  # type: ignore[index]
        self.assertEqual(r.names(), ["disabled.any", "email.send", "notes.read"])


if __name__ == "__main__":
    unittest.main()
