"""What "the value must appear in the user's own text" accepts, exactly.

Accepted: the same characters, in the same case, found in the text as whole words: starting and ending
at a space or an edge of the text, with quotes or brackets around it and sentence punctuation after it set aside.
Refused: anything else - another case, another Unicode form, a look-alike, a piece cut out of a longer word,
number, address or path.
Still accepted, and stated as a limit: one word out of a longer phrase ("Jane" out of "Jane Citizen")."""
import unicodedata
import unittest

from oathline import Capability, Registry, validate

R = Registry([Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True)])


def kept(text, value):
    v = validate({"intent": "email.send", "arguments": {"x": value}}, text, R, allowed_arguments=("x",))
    return v.arguments.get("x"), v.violations


class VerbatimTest(unittest.TestCase):
    def test_exact_whole_words_are_kept_unchanged(self):
        for text, value in (("refund order A-1001 for Bob", "A-1001"), ("refund order A-1001 for Bob", "Bob"),
                            ("email bob@example.com the notes", "bob@example.com"),
                            ("send 100 dollars", "100"), ("send 100 dollars", 100), ("pay 2.5 now", 2.5),
                            ("call Jane Citizen today", "Jane Citizen"), ("café booking", "café"),
                            ("Bob", "Bob"), ("(Bob)", "Bob"), ("say \"hello there\" twice", "hello there")):
            value_kept, violations = kept(text, value)
            self.assertEqual(value_kept, str(value), (text, value))
            self.assertEqual(violations, [])

    def test_a_piece_of_a_longer_word_or_number_is_refused(self):
        for text, value in (("send 100 dollars", "1"), ("send 100 dollars", "10"), ("send 100 dollars", 1),
                            ("refund order A-1001", "A-100"), ("refund order A-1001", "A-1"),
                            ("email bob@example.com", "b@example.com"), ("email bob@example.com", "ob"),
                            ("pay the banana man", "nan"), ("pay the banana man", "ban"),
                            ("transfer to account 12345678", "1234"), ("meet Alexander", "Alex")):
            value_kept, violations = kept(text, value)
            self.assertIsNone(value_kept, (text, value))
            self.assertEqual(violations, ["argument_not_verbatim:x"])

    def test_another_case_is_refused(self):
        for text, value in (("refund Bob", "bob"), ("refund Bob", "BOB"), ("order a-1001", "A-1001")):
            self.assertIsNone(kept(text, value)[0], (text, value))

    def test_another_unicode_form_or_a_look_alike_is_refused(self):
        nfc, nfd = "café", unicodedata.normalize("NFD", "café")
        self.assertNotEqual(nfc, nfd)
        for text, value in ((nfc + " booking", nfd), (nfd + " booking", nfc),
                            ("email bob@kmail.com", "bob@Kmail.com"),            # Kelvin sign for k
                            ("email bob@kmail.com", "bob@Kmail.com"),
                            ("pay alice", "аlice"),                              # Cyrillic a
                            ("pay alice", "ａlice"),                              # fullwidth a
                            ("pay alice", "alice​"), ("pay alice", "al​ice"),
                            ("straße 5", "strasse")):
            self.assertIsNone(kept(text, value)[0], (text, value))

    def test_the_stated_limit_one_word_out_of_a_longer_phrase_is_accepted(self):
        for text, value in (("pay Jane Citizen", "Jane"), ("don't cancel the order", "cancel"),
                            ("send it to bob not alice", "alice")):
            self.assertEqual(kept(text, value)[0], value, (text, value))

    def test_exactly_which_pieces_are_kept(self):
        """The stated limits of the whole-word rule, each one shown."""
        # 1. Words are split at white space of any kind, so what the user wrote with a space in it is several words.
        for text, value in (("pay 1 000,50 EUR to bob", "1"), ("pay 1 000,50 EUR to bob", "000,50"),
                            ("card 4111 1111 1111 1111", "1111"), ("call +61 400 123 456", "400"),  # gitleaks:allow
                            ("rm /home/bob/my docs/old", "/home/bob/my"), ("send to bob smith@corp.com", "smith@corp.com"),
                            ("pay 100 000 kr", "100"), ("pay 1 000 now", "1"), ("pay 1　000 now", "1"),
                            ("pay 1\t000 now", "1"), ("pay 1\n000 now", "1")):
            self.assertEqual(kept(text, value)[0], value, (text, value))
        # 2. Any white space between words matches any other white space.
        for text in ("Jane Citizen", "Jane  Citizen", "Jane\tCitizen", "Jane\nCitizen", "Jane Citizen"):
            self.assertEqual(kept("pay " + text + " now", "Jane Citizen")[0], "Jane Citizen", repr(text))
        # 3. Closing punctuation and quotes or brackets around a value are set aside, so a value that differs
        #    from the user's word only by those is kept.
        for text, value in (("the U.S. market", "U.S"), ("is it Bob?!", "Bob"), ("pay (100).", "100"),
                            ("say 'yes'", "yes"), ("order A-1001,", "A-1001")):
            self.assertEqual(kept(text, value)[0], value, (text, value))
        # 4. Nothing else is set aside: an opening or middle character is part of the word.
        for text, value in (("pay $100", "100"), ("pay #42", "42"), ("see @bob", "bob"), ("a.b.c", "b"),
                            ("x=5", "5"), ("pay 100%", "100"), ("the U.S. market", "U"), ("pay 1​000", "1")):
            self.assertIsNone(kept(text, value)[0], (text, value))

    def test_values_that_are_not_text_or_finite_numbers_are_refused(self):
        for value in (None, True, False, float("nan"), float("inf"), ["Bob"], {"a": "Bob"}, b"Bob", ""):
            value_kept, _ = kept("Bob nan inf True None", value)
            self.assertIsNone(value_kept, repr(value))

    def test_very_long_text_and_values(self):
        import time
        text = ("word " * 9_000) + "needle-42 " + ("word " * 9_000)              # just under the 100,000 limit
        self.assertEqual(kept(text, "needle-42")[0], "needle-42")
        self.assertIsNone(kept(text, "needle-4")[0])
        self.assertIsNone(kept(text, "x" * 1_000_000)[0])
        self.assertIsNone(kept("short", "short" * 100_000)[0])
        huge = ("word " * 400_000) + "needle-42"                                 # about 2 MB: over the limit
        value_kept, violations = kept(huge, "needle-42")
        self.assertIsNone(value_kept)
        self.assertEqual(violations, ["text_too_long"])
        started = time.perf_counter()                                            # the worst case stays bounded
        reg_text = "a " * 49_999
        validate({"intent": "email.send", "arguments": {f"k{i}": "a a a a b" for i in range(100)}}, reg_text, R,
                 allowed_arguments=tuple(f"k{i}" for i in range(100)))
        self.assertLess(time.perf_counter() - started, 30)

    def test_surrounding_spaces_in_the_models_value_are_not_part_of_the_match(self):
        self.assertEqual(kept("refund Bob now", "  Bob ")[0], "Bob")


if __name__ == "__main__":
    unittest.main()
