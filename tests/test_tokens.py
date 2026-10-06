import unittest

from oathline import ConfigError, TokenStore


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class TokenTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s = TokenStore(ttl_seconds=300, clock=self.clock)

    def test_confirm_returns_stored_arguments_once(self):
        t = self.s.propose("agent", "email.send", {"to": "a@example.com"})
        c = self.s.confirm("agent", t)
        self.assertTrue(c.ok)
        self.assertEqual(c.arguments, {"to": "a@example.com"})
        self.assertEqual(self.s.confirm("agent", t).error, "already_used")

    def test_expired_never_runs(self):
        t = self.s.propose("agent", "email.send", {})
        self.clock.t += 301
        self.assertEqual(self.s.confirm("agent", t).error, "expired")
        self.assertEqual(self.s.get(t)["state"], "expired")

    def test_wrong_principal_refused(self):
        t = self.s.propose("agent", "email.send", {})
        self.assertEqual(self.s.confirm("mallory", t).error, "wrong_principal")

    def test_unknown_token(self):
        self.assertEqual(self.s.confirm("agent", "tok_nope").error, "unknown_token")

    def test_tampered_arguments_refused(self):
        t = self.s.propose("agent", "email.send", {"to": "a@example.com"})
        self.s._db.execute("UPDATE proposals SET arguments=? WHERE id=?", ('{"to": "evil@example.com"}', t))
        self.assertEqual(self.s.confirm("agent", t).error, "tampered")

    def test_ttl_bounds(self):
        for bad in (0, 29, 3601, 30.5, "300"):
            with self.assertRaises(ConfigError):
                TokenStore(ttl_seconds=bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
