"""Inputs that once made a call raise, slip through or go unrecorded. One test for each."""
import os
import sqlite3
import sys
import tempfile
import unicodedata
import unittest

from oathline import AuditLog, Capability, Engine, Registry, TokenStore, is_non_human, validate
from oathline.capabilities import name_problem

HIDDEN = ["͏", "ᅟ", "ᅠ", "឴", "឵", "᠋", "᠏", "ㅤ", "️", "ﾠ",
          "\U000e0100", "⠀", "​", "­", "؜", "⁠", "﻿", "\U000e0001", "\U0001d173"]


def make(**kwargs):
    reg = Registry([Capability("notes.read", frozenset({"agent", "alice", "support-bot"})),
                    Capability("email.send", frozenset({"agent", "alice", "support-bot"}), writes=True,
                               needs_confirmation=True)])
    eng = Engine(reg, **kwargs)
    ran = []
    eng.register("notes.read", lambda a: ran.append("read") or {"n": 1})
    eng.register("email.send", lambda a: ran.append("send") or {"n": 1})
    return eng, ran


class InputsNeverRaiseTest(unittest.TestCase):
    def test_many_small_arguments_for_an_undeclared_capability_are_refused_by_record_not_by_error(self):
        """Masking every value made the log entry too large and request() raised."""
        for count in (400, 480, 700, 800):
            eng, ran = make()
            args = {f"k{i}": 1 for i in range(count)}
            for call, who in ((eng.request, "agent"), (eng.propose, "agent"), (eng.request, "mallory")):
                out = call(who, "no.such", args)
                self.assertEqual((out["ok"], out["error"]), (False, "denied"), count)
            events = eng.audit.events()
            self.assertEqual([e["event_type"] for e in events], ["REQUEST", "DENIED"] * 3, count)
            self.assertTrue(eng.audit.verify()["ok"])
            self.assertEqual(ran, [])

    def test_many_secret_arguments_on_a_declared_capability_do_not_raise_either(self):
        names = frozenset(f"k{i}" for i in range(600))
        reg = Registry([Capability("vault.read", frozenset({"agent"}), secret_arguments=names)])
        eng = Engine(reg)
        eng.register("vault.read", lambda a: {"n": len(a)})
        out = eng.request("agent", "vault.read", {k: 1 for k in names})
        self.assertTrue(out["ok"])
        self.assertTrue(eng.audit.verify()["ok"])

    def test_confirm_refuses_instead_of_raising_when_the_token_store_is_unavailable(self):
        """A locked or closed token store made confirm() raise a raw sqlite error."""
        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        eng.tokens.close()
        out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "token_store_unavailable"))
        self.assertEqual(eng.audit.events()[-1]["payload"]["reason"], "token_store_unavailable")
        self.assertEqual(ran, [])
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "tokens.db")
            eng2, ran2 = make(tokens=TokenStore(path))
            eng2.tokens._db.execute("PRAGMA busy_timeout=200")
            tok2 = eng2.request("agent", "email.send", {})["token"]
            blocker = sqlite3.connect(path)
            blocker.execute("BEGIN EXCLUSIVE")
            try:
                out = eng2.confirm("alice", tok2)
                self.assertEqual((out["ok"], out["error"]), (False, "token_store_unavailable"))
                self.assertEqual(ran2, [])
            finally:
                blocker.rollback()
                blocker.close()
            self.assertTrue(eng2.confirm("alice", tok2)["ok"])              # the token is still good afterwards
            eng2.tokens.close()

    def test_a_stored_row_with_an_unusable_name_is_refused_not_raised(self):
        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        eng.tokens._db.execute("UPDATE proposals SET capability=? WHERE id=?", ("x" * 200, tok))
        out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "corrupt_stored_row"))
        self.assertEqual(ran, [])

    def test_text_and_object_subclasses_are_refused(self):
        """A str subclass that lies about itself confirmed as a human; others made calls raise."""
        class Liar(str):
            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash("alice")

        class Angry(str):
            def strip(self, *a):
                raise RuntimeError("no")

        class AngryDict(dict):
            def values(self):
                raise RuntimeError("no")

        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        for fake in (Liar("agent"), Angry("alice")):
            self.assertFalse(eng.confirm(fake, tok)["ok"])
            self.assertFalse(eng.request(fake, "notes.read")["ok"])
        self.assertEqual(eng.request("agent", "notes.read", AngryDict(a=1))["error"], "bad_arguments")
        self.assertEqual(eng.request("agent", "notes.read", {"x": AngryDict(a=1)})["error"], "arguments_not_storable")
        self.assertEqual(ran, [])
        self.assertTrue(eng.confirm("alice", tok)["ok"])


class HiddenCharactersTest(unittest.TestCase):
    def test_every_hidden_character_we_know_of_is_refused_in_a_name(self):
        """Fillers and variation selectors were accepted in names."""
        for ch in HIDDEN:
            for name in ("agent" + ch, ch + "alice", "ali" + ch + "ce"):
                self.assertEqual(name_problem(name), "hidden_character", f"U+{ord(ch):04X}")
            self.assertTrue(is_non_human("agent" + ch), f"U+{ord(ch):04X}")
            self.assertTrue(is_non_human("ag" + ch + "ent"), f"U+{ord(ch):04X}")
            with self.assertRaises(ValueError):
                Registry([Capability("x.read", frozenset({"alice" + ch}))])
            eng, ran = make()
            self.assertEqual(eng.request("alice" + ch, "notes.read")["error"], "bad_principal")
            self.assertEqual(ran, [])

    def test_sweep_of_all_code_points_by_the_stated_rule(self):
        """The rule, exactly: outside ASCII a name may hold only letters, marks and digits, and never a
        character from the default-ignorable list. Checked for every code point."""
        allowed_non_ascii = 0
        for cp in range(0x110000):
            ch = chr(cp)
            cat = unicodedata.category(ch)
            ok = name_problem("a" + ch + "b") is None
            if cp < 0x80:
                self.assertEqual(ok, 0x20 <= cp < 0x7F, hex(cp))
            else:
                if ok:
                    allowed_non_ascii += 1
                    self.assertIn(cat[0], "LMN", hex(cp))
                if cat[0] in "CZSP":
                    self.assertFalse(ok, hex(cp))
        self.assertGreater(allowed_non_ascii, 100_000)                       # ordinary letters of every script still work
        for fine in ("José", "李雷", "अनिल", "user:42", "agent@host", "Jane Citizen"):
            self.assertIsNone(name_problem(fine), fine)


class BlankAndJoinerCharactersTest(unittest.TestCase):
    TEN = ["\U00013441", "\U00013442", "\U00013440", "\U00016fe4", "\U0001bc9d", "⵿", "\U0001107f",
           "\U00011a47", "\U00011a99", "\U00011f42"]

    def test_blank_filler_joiner_and_selector_characters_are_refused_in_a_name(self):
        for ch in self.TEN:
            if not unicodedata.name(ch, ""):
                continue                                   # this Python's Unicode table has no name for it: refused for that
            self.assertEqual(name_problem("agent" + ch), "hidden_character", f"U+{ord(ch):04X}")
            self.assertTrue(is_non_human("agent" + ch), f"U+{ord(ch):04X}")
        for ch in self.TEN:
            self.assertIsNotNone(name_problem("agent" + ch), f"U+{ord(ch):04X}")

    def test_no_accepted_character_is_named_as_blank_filler_joiner_or_selector(self):
        words = ("BLANK", "FILLER", "JOINER", "SELECTOR", "INVISIBLE", "ZERO WIDTH", "MIRROR")
        for cp in range(0x80, 0x110000):
            ch = chr(cp)
            if name_problem("a" + ch + "b") is None:
                name = unicodedata.name(ch, "")
                self.assertTrue(name, hex(cp))                                    # every accepted character has a name
                self.assertFalse(any(w in name for w in words), (hex(cp), name))


class CorruptStoredRowTest(unittest.TestCase):
    def test_a_stored_row_that_cannot_be_read_as_a_proposal_has_its_own_error(self):
        for column, value in (("expires", "abc"), ("created", "zz"), ("created", None), ("expires", b"\x00"),
                              ("state", "weird"), ("state", 5), ("arguments", "not json"), ("arguments", "[1]"),
                              ("arguments", b"\x00"), ("integrity", 5), ("integrity", None),
                              ("principal", "x" * 200), ("principal", b"agent"), ("capability", "bad\x00name")):
            eng, ran = make()
            tok = eng.request("agent", "email.send", {"to": "bob"})["token"]
            try:
                eng.tokens._db.execute(f"UPDATE proposals SET {column}=? WHERE id=?", (value, tok))
            except Exception:                              # the column refuses the value (NOT NULL): nothing to test
                continue
            for _ in range(2):                             # trying again gives the same answer
                out = eng.confirm("alice", tok)
                self.assertEqual((out["ok"], out["error"]), (False, "corrupt_stored_row"), (column, value))
            self.assertEqual(eng.audit.events()[-1]["payload"]["reason"], "corrupt_stored_row")
            self.assertEqual(ran, [], (column, value))

    def test_a_readable_row_whose_arguments_no_longer_match_its_hash_is_still_tampered(self):
        eng, ran = make()
        tok = eng.request("agent", "email.send", {"to": "bob"})["token"]
        eng.tokens._db.execute("UPDATE proposals SET arguments=? WHERE id=?", ('{"to": "eve"}', tok))
        self.assertEqual(eng.confirm("alice", tok)["error"], "tampered")
        self.assertEqual(ran, [])

    def test_the_token_store_alone_gives_the_same_two_answers(self):
        store = TokenStore()
        tok = store.propose("alice", "email.send", {"to": "bob"})
        store._db.execute("UPDATE proposals SET expires='abc' WHERE id=?", (tok,))
        self.assertEqual(store.confirm("alice", tok).error, "corrupt_stored_row")
        tok2 = store.propose("alice", "email.send", {"to": "bob"})
        store._db.execute("UPDATE proposals SET arguments=? WHERE id=?", ('{"to": "eve"}', tok2))
        self.assertEqual(store.confirm("alice", tok2).error, "tampered")


class RegistryTest(unittest.TestCase):
    def test_attributes_of_a_built_registry_cannot_be_deleted_or_rebound(self):
        """`del reg._caps` followed by an assignment once widened a grant."""
        reg = Registry([Capability("pay", frozenset({"alice"}))])
        with self.assertRaises(TypeError):
            del reg._caps
        with self.assertRaises(TypeError):
            reg._caps = {}
        with self.assertRaises(TypeError):
            reg.extra = 1
        with self.assertRaises(Exception):
            reg.authorize("mallory", "pay")


class HumansListTest(unittest.TestCase):
    def test_an_agent_with_a_name_of_its_own_can_confirm_unless_the_engine_is_told(self):
        """A name that is not on the non-human list counts as human."""
        eng, ran = make()
        tok = eng.request("support-bot", "email.send", {})["token"]
        self.assertTrue(eng.confirm("support-bot", tok)["ok"])                 # the limit, shown

    def test_with_a_humans_list_only_listed_names_can_confirm(self):
        eng, ran = make(humans=frozenset({"alice"}))
        tok = eng.request("support-bot", "email.send", {})["token"]
        out = eng.confirm("support-bot", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "not_a_listed_human"))
        self.assertEqual(eng.confirm("mallory", tok)["error"], "not_a_listed_human")
        self.assertEqual(ran, [])
        self.assertEqual(eng.audit.events()[-1]["payload"]["reason"], "not_a_listed_human")
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(ran, ["send"])

    def test_a_name_on_the_humans_list_is_never_treated_as_an_agent_proposer(self):
        """With humans listed, everyone else is an agent: their gated requests are agent proposals."""
        eng, ran = make(humans=frozenset({"alice"}))
        eng.request("support-bot", "email.send", {})
        eng.request("alice", "email.send", {})
        kinds = [e["event_type"] for e in eng.audit.events() if e["event_type"].startswith("PROPOSED")]
        self.assertEqual(kinds, ["PROPOSED_BY_AGENT", "PROPOSED"])

    def test_with_a_humans_list_junk_names_are_still_refused_not_raised(self):
        eng, ran = make(humans=frozenset({"alice"}))
        for junk in (["alice"], {"a": 1}, None, 5, b"alice", ("alice",)):
            self.assertFalse(eng.propose(junk, "email.send", {})["ok"])
            self.assertFalse(eng.request(junk, "email.send", {})["ok"])
            self.assertFalse(eng.confirm(junk, "tok_" + "0" * 32)["ok"])
        self.assertEqual(ran, [])

    def test_humans_must_be_a_set_of_plain_names_and_not_overlap_the_non_human_list(self):
        with self.assertRaises(TypeError):
            Engine(Registry([]), humans="alice")
        with self.assertRaises(ValueError):
            Engine(Registry([]), humans=frozenset({"alice "}))
        with self.assertRaises(ValueError):
            Engine(Registry([]), humans=frozenset({"agent"}))


class ValidatorTest(unittest.TestCase):
    R = Registry([Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True)])

    def kept(self, text, value):
        v = validate({"intent": "email.send", "arguments": {"x": value}}, text, self.R, allowed_arguments=("x",))
        return v.arguments.get("x")

    def test_pieces_of_numbers_addresses_paths_and_words_are_refused(self):
        """Separators, signs, underscores, apostrophes and combining marks let pieces through."""
        for text, value in (("pay 1,000.50 to bob", "1"), ("pay 1,000.50 to bob", "000"), ("pay 100.50 to bob", "100"),
                            ("pay 100.50 to bob", 50), ("refund -500 to bob", 500), ("refund -500 to bob", "500"),
                            ("send to bob_admin@corp.com", "admin@corp.com"), ("rm ~/old/*.log", "~"),
                            ("rm ~/old/*.log", "*"), ("non-refundable order", "refundable"),
                            ("email bob@example.com", "example.com"), ("refund order A-1001", "1001"),
                            (unicodedata.normalize("NFD", "café") + " booking", "cafe"),
                            ("किताब दो", "क"), ("see mailto:bob@x.com", "bob@x.com")):
            self.assertIsNone(self.kept(text, value), (text, value))

    def test_whole_words_are_kept_with_wrapping_punctuation_set_aside(self):
        for text, value in (("pay 1,000.50 to bob", "1,000.50"), ("refund -500 to bob", "-500"),
                            ("refund -500 to bob", -500), ("order A-1001.", "A-1001"), ("call (Bob) now", "Bob"),
                            ("say \"hello there\" twice", "hello there"), ("is it Bob?", "Bob"),
                            ("mail <bob@example.com>, please", "bob@example.com"), ("the U.S. market", "U.S."),
                            ("delete /tmp/build not /", "/"), ("pay  Jane   Citizen today", "Jane Citizen"),
                            ("don't cancel", "cancel"), ("pay Jane Citizen", "Jane")):
            self.assertEqual(self.kept(text, value), str(value), (text, value))

    def test_with_too_many_candidates_none_is_kept_not_even_the_intent(self):
        v = validate({"intent": "email.send", "candidates": ["x"] * 101}, "t", self.R)
        self.assertEqual(v.candidates, [])
        self.assertIn("too_many_candidates", v.violations)

    def test_wrong_types_are_reported_not_only_dropped(self):
        for bad in (5, ["email.send"], {"a": 1}, True, 2.5):
            v = validate({"intent": bad}, "t", self.R)
            self.assertEqual(v.candidates, [])
            self.assertIn("intent_not_text", v.violations, repr(bad))
        for bad in ([], 0, "", "text", 5, False, [1]):
            v = validate({"intent": "email.send", "arguments": bad}, "t", self.R)
            self.assertEqual(v.arguments, {})
            self.assertIn("arguments_not_an_object", v.violations, repr(bad))
        self.assertEqual(validate({"intent": None, "arguments": None}, "t", self.R).violations, [])

    def test_text_over_the_limit_keeps_no_arguments(self):
        text = ("word " * 30_000) + "needle-42"
        v = validate({"intent": "email.send", "arguments": {"x": "needle-42"}}, text, self.R, allowed_arguments=("x",))
        self.assertEqual(v.arguments, {})
        self.assertIn("text_too_long", v.violations)


class RecordTest(unittest.TestCase):
    def test_a_token_that_ran_twice_is_listed(self):
        """Resetting a used token left nothing mismatched_runs() could find."""
        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(eng.mismatched_runs(), [])
        eng.tokens._db.execute("UPDATE proposals SET state='proposed' WHERE id=?", (tok,))      # a token-store writer
        self.assertTrue(eng.confirm("alice", tok)["ok"])                                         # the stated limit
        found = eng.mismatched_runs()
        self.assertEqual([(f["token"], f["reason"]) for f in found], [(tok, "ran_more_than_once")])

    def test_a_correctly_hashed_event_added_at_the_end_needs_an_external_head(self):
        """The stated limit, shown. Someone with the file can append a forged event."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "audit.db")
            with AuditLog(path) as log:
                log.append("EVENT", "alice", "demo", {"i": 1})
                head = log.head()
            with AuditLog(path) as forger:                       # the forger simply uses the library
                forger.append("CONFIRMED_BY_HUMAN", "alice", "pay.send", {"token": "tok_" + "0" * 32})
            with AuditLog(path) as log:
                self.assertTrue(log.verify()["ok"])
                self.assertFalse(log.verify(expected_head=head)["ok"])

    def test_the_log_reading_helpers_do_not_fail_on_an_event_added_by_hand(self):
        eng, ran = make()
        tok = eng.request("agent", "email.send", {})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        for payload in ({"token": ["x"], "arguments_hash": {"a": 1}, "executing_seq": [1]}, {"token": 5}, {}):
            eng.audit.append("EXECUTING", "agent", "email.send", payload)
            eng.audit.append("EXECUTED", "agent", "email.send", payload)
            eng.audit.append("PROPOSED", "agent", "email.send", payload)
        eng.audit._db.execute("DROP TRIGGER audit_no_update")
        eng.audit._db.execute("UPDATE audit SET payload='[]' WHERE seq=(SELECT MAX(seq) FROM audit)")
        self.assertEqual(eng.mismatched_runs(), [])
        self.assertEqual(len(eng.unfinished()), 3)                      # the hand-made EXECUTING events have no outcome

    def test_verify_reports_an_unreadable_row_instead_of_raising(self):
        """A text column changed to a blob made verify() raise."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "audit.db")
            with AuditLog(path) as log:
                for i in range(3):
                    log.append("EVENT", "alice", "demo", {"i": i})
            for sql, args in (("UPDATE audit SET principal=? WHERE seq=1", (b"alice",)),
                              ("UPDATE audit SET event_type=? WHERE seq=1", (b"EVENT",)),
                              ("UPDATE audit SET chain_id=? WHERE seq=0", (b"oathline-audit-001",)),
                              ("UPDATE audit SET ts=? WHERE seq=2", (b"\x00\x01",)),
                              ("UPDATE audit SET payload=? WHERE seq=1", ("[" * 100_000,))):
                c = sqlite3.connect(path)
                c.execute("DROP TRIGGER IF EXISTS audit_no_update")
                c.execute(sql, args)
                c.commit()
                c.close()
                with AuditLog(path) as log:
                    out = log.verify()
                    self.assertFalse(out["ok"], sql)
                    self.assertTrue(out["error"], sql)


if __name__ == "__main__":
    unittest.main()
