"""Time: expiry at the exact boundary, and a clock that jumps backwards. An expired token never runs."""
import unittest

from oathline import Capability, Engine, Registry, TokenStore


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(clock, ttl=60):
    reg = Registry([Capability("pay.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg, tokens=TokenStore(ttl_seconds=ttl, clock=clock))
    ran = []
    eng.register("pay.send", lambda a: ran.append(1) or {"paid": 1})
    return eng, ran


class TimeTest(unittest.TestCase):
    def test_the_boundary_exactly(self):
        """A token lives for ttl seconds, the last instant included; any time after that it is expired."""
        for offset, runs in ((0, True), (59.999, True), (60, True), (60.000001, False), (61, False), (10 ** 9, False)):
            clock = Clock()
            eng, ran = make(clock)
            tok = eng.request("agent", "pay.send", {})["token"]
            clock.t += offset
            out = eng.confirm("alice", tok)
            self.assertEqual(out["ok"], runs, offset)
            self.assertEqual(len(ran), 1 if runs else 0, offset)
            if not runs:
                self.assertEqual(out["error"], "expired")

    def test_once_seen_expired_a_token_stays_expired_when_the_clock_goes_back(self):
        clock = Clock()
        eng, ran = make(clock)
        tok = eng.request("agent", "pay.send", {})["token"]
        clock.t += 61
        self.assertEqual(eng.confirm("alice", tok)["error"], "expired")
        for back in (60, 61, 3600, 10 ** 6):
            clock.t = 1_000_000.0 + 61 - back
            self.assertEqual(eng.confirm("alice", tok)["error"], "expired", back)
        self.assertEqual(ran, [])
        self.assertEqual(eng.tokens.get(tok)["state"], "expired")

    def test_a_clock_set_back_before_the_proposal_time_does_not_run_the_token(self):
        """A clock earlier than the moment the token was created means the clock cannot be trusted: refused."""
        clock = Clock()
        eng, ran = make(clock)
        tok = eng.request("agent", "pay.send", {})["token"]
        clock.t -= 5
        out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "clock_went_back"))
        self.assertEqual(ran, [])
        clock.t += 10                                           # the clock is sane again: the token is still good
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(len(ran), 1)

    def test_the_stated_limit_a_clock_set_back_inside_the_lifetime_extends_it(self):
        """The store only knows the clock it is given. If the clock is set back, but not to before the
        proposal, and nobody has tried the token since it expired, the token is still inside its lifetime."""
        clock = Clock()
        eng, ran = make(clock)
        tok = eng.request("agent", "pay.send", {})["token"]
        clock.t += 500                                          # really expired, but nobody tried it
        clock.t -= 470                                          # an operator or NTP sets the clock back
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(len(ran), 1)

    def test_a_broken_clock_is_a_refusal(self):
        for bad in (float("nan"), None, "now"):
            clock = Clock()
            eng, ran = make(clock)
            tok = eng.request("agent", "pay.send", {})["token"]
            clock.t = bad
            out = eng.confirm("alice", tok)
            self.assertFalse(out["ok"], repr(bad))
            self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
