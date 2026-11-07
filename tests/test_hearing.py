"""The hearing rule (0.2.0): nothing is written unheard. A capability that declares `heard=True` runs only after the
human was SHOWN exactly what will be written, HEARD it, and said yes to the items as they stand at that moment; every
step is on the record. A capability that does not declare it behaves exactly as in 0.1.2."""
import unittest

from oathline import Capability, Registry


def gated(name="email.send", **kw):
    return Capability(name, frozenset({"agent", "alice"}), writes=True, needs_confirmation=True, **kw)


class HeardFlagTest(unittest.TestCase):
    def test_heard_is_off_by_default(self):
        self.assertFalse(gated().heard)
        self.assertFalse(Capability("notes.read", frozenset({"alice"})).heard)

    def test_a_heard_capability_must_be_gated(self):
        with self.assertRaises(ValueError):
            Registry([Capability("notes.read", frozenset({"alice"}), heard=True)])

    def test_a_gated_capability_may_declare_heard(self):
        reg = Registry([gated(heard=True)])
        self.assertTrue(reg.get("email.send").heard)
        self.assertTrue(reg.get("email.send").gated)


if __name__ == "__main__":
    unittest.main()
