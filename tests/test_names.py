"""Names: principals, approvers, capabilities, the non-human list. Odd names are refused, never guessed at."""
import unittest

from oathline import Capability, Engine, Registry, is_non_human

ZERO_WIDTH = "​"
BAD_NAMES = [" alice", "alice ", "\talice", "alice\n", "ali\x00ce", "alice" + ZERO_WIDTH, ZERO_WIDTH + "alice",
             "ali‮ce", "alice ", " alice", "ali ce", "", "   ", "a" * 129, 5, None, b"alice",
             ["alice"], ("alice",), 1.5, True]


def make():
    reg = Registry([Capability("notes.read", frozenset({"agent", "alice"})),
                    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True,
                               approvers=frozenset({"alice"}))])
    eng = Engine(reg)
    ran = []
    eng.register("notes.read", lambda a: ran.append("read") or {"notes": 1})
    eng.register("email.send", lambda a: ran.append("send") or {"sent": 1})
    return eng, ran


class NamesTest(unittest.TestCase):
    def test_bad_principal_is_refused_and_recorded_on_request_and_propose(self):
        for bad in BAD_NAMES:
            eng, ran = make()
            for call in (eng.request, eng.propose):
                out = call(bad, "notes.read")
                self.assertEqual((out["ok"], out["error"]), (False, "bad_principal"), repr(bad))
            self.assertEqual(ran, [], repr(bad))
            self.assertEqual({e["event_type"] for e in eng.audit.events()}, {"REQUEST_REFUSED"}, repr(bad))
            self.assertTrue(eng.audit.verify()["ok"])

    def test_bad_capability_name_is_refused_and_recorded(self):
        for bad in BAD_NAMES:
            eng, ran = make()
            for call in (eng.request, eng.propose):
                out = call("agent", bad)
                self.assertEqual((out["ok"], out["error"]), (False, "bad_capability"), repr(bad))
            self.assertEqual(ran, [])
            self.assertEqual({e["event_type"] for e in eng.audit.events()}, {"REQUEST_REFUSED"}, repr(bad))

    def test_bad_name_cannot_confirm(self):
        for bad in BAD_NAMES:
            eng, ran = make()
            tok = eng.request("agent", "email.send", {})["token"]
            out = eng.confirm(bad, tok)
            self.assertFalse(out["ok"], repr(bad))
            self.assertIn(out["error"], ("bad_principal", "non_human_principal"), repr(bad))
            self.assertEqual(ran, [], repr(bad))
            self.assertEqual(eng.audit.events()[-1]["event_type"], "CONFIRM_REFUSED")
            self.assertTrue(eng.confirm("alice", tok)["ok"])         # the refusals did not use the token up

    def test_bad_token_is_an_unknown_token(self):
        eng, ran = make()
        for bad in (None, 5, b"tok", ["tok_x"], {"t": 1}, "", "tok_" + "a" * 200, "tok_\x00", 1.5):
            out = eng.confirm("alice", bad)
            self.assertEqual((out["ok"], out["error"]), (False, "unknown_token"), repr(bad))
        self.assertEqual(ran, [])
        self.assertTrue(eng.audit.verify()["ok"])

    def test_names_are_compared_exactly_so_another_case_or_a_look_alike_gets_nothing(self):
        eng, ran = make()
        cyrillic_a = "аlice"                                     # looks like "alice"
        for other in ("Alice", "ALICE", cyrillic_a, "ａlice", "alicе"):
            self.assertEqual(eng.request(other, "notes.read")["error"], "denied", repr(other))
            tok = eng.request("agent", "email.send", {})["token"]
            self.assertEqual(eng.confirm(other, tok)["error"], "not_an_approver", repr(other))
        self.assertEqual(ran, [])

    def test_non_human_list_matches_through_case_width_spaces_and_hidden_characters(self):
        for name in ("agent", "Agent", "AGENT", " agent ", "agent@host", "ａgent", "agent" + ZERO_WIDTH,
                     "ag‍ent", "\tagent\n", "Agent@HOST", "", "  ", None, 5, b"agent", ["agent"]):
            self.assertTrue(is_non_human(name), repr(name))
        for listed in ("Builder-Agent", " builder-agent ", "Ｂuilder-agent", "builder-agent" + ZERO_WIDTH):
            self.assertTrue(is_non_human("builder-agent", [listed]), repr(listed))
        self.assertFalse(is_non_human("alice"))

    def test_a_look_alike_of_a_non_human_name_is_a_different_name_and_gets_nothing(self):
        eng, ran = make()
        fake_agent = "аgent"                                     # Cyrillic a: not on the list, and not granted
        tok = eng.request("agent", "email.send", {})["token"]
        self.assertEqual(eng.confirm(fake_agent, tok)["error"], "not_an_approver")
        self.assertEqual(eng.request(fake_agent, "notes.read")["error"], "denied")
        self.assertEqual(ran, [])

    def test_registry_refuses_odd_names(self):
        for bad in (" alice", "alice ", "ali\x00ce", "alice" + ZERO_WIDTH, "", "a" * 129):
            with self.assertRaises(ValueError, msg=repr(bad)):
                Registry([Capability(bad, frozenset({"alice"}))])
            with self.assertRaises(ValueError, msg=repr(bad)):
                Registry([Capability("x.read", frozenset({bad}))])
            with self.assertRaises(ValueError, msg=repr(bad)):
                Registry([Capability("x.send", frozenset({"alice"}), writes=True, needs_confirmation=True,
                                     approvers=frozenset({bad}))])

    def test_name_sets_must_be_sets_of_text(self):
        with self.assertRaises(TypeError):
            Capability("pay", "alice-admin")                          # a string is not a set of names
        with self.assertRaises(TypeError):
            Capability("pay", frozenset({"alice"}), approvers="alice")
        with self.assertRaises(TypeError):
            Capability("pay", frozenset({"alice"}), secret_arguments="token")
        with self.assertRaises(TypeError):
            Capability("pay", frozenset({"alice", 5}))
        with self.assertRaises(TypeError):
            Engine(Registry([]), non_human="builder")
        with self.assertRaises(TypeError):
            Engine(Registry([]), non_human=["builder", 5])

    def test_a_plain_set_is_frozen_so_a_grant_cannot_be_widened_through_it(self):
        mine = {"alice"}
        cap = Capability("pay", mine, writes=True, needs_confirmation=True, approvers={"alice"})
        mine.add("mallory")
        self.assertEqual(cap.principals, frozenset({"alice"}))
        self.assertIsInstance(cap.principals, frozenset)
        self.assertIsInstance(cap.approvers, frozenset)
        with self.assertRaises(AttributeError):
            cap.principals.add("mallory")

    def test_a_registry_cannot_be_rebuilt_in_place(self):
        reg = Registry([Capability("pay", frozenset({"alice"}))])
        with self.assertRaises(TypeError):
            reg.__init__([Capability("pay", frozenset({"alice", "mallory"}))])
        with self.assertRaises(Exception):
            reg.authorize("mallory", "pay")


if __name__ == "__main__":
    unittest.main()
