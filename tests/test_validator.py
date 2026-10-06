import unittest

from oathline import Capability, Registry, validate

R = Registry([Capability("calendar.read", frozenset({"agent"})),
              Capability("email.send", frozenset({"agent"}), writes=True, needs_confirmation=True)])
TEXT = "Email Jane Citizen the agenda for Tuesday"


class ValidatorTest(unittest.TestCase):
    def test_keeps_known_capabilities_only(self):
        v = validate({"intent": "email.send", "candidates": ["calendar.read", "wire.money"]}, TEXT, R)
        self.assertEqual(v.candidates, ["email.send", "calendar.read"])
        self.assertIn("unknown_capability:wire.money", v.violations)

    def test_model_cannot_create_authority(self):
        v = validate({"intent": "email.send", "approved": True, "permissions": ["all"]}, TEXT, R)
        self.assertIn("forbidden_field:approved", v.violations)
        self.assertIn("forbidden_field:permissions", v.violations)
        self.assertFalse(v.clean)

    def test_arguments_must_be_declared_and_verbatim(self):
        v = validate({"intent": "email.send",
                      "arguments": {"recipient": "Jane Citizen", "day": "Wednesday", "url": "evil.example"}},
                     TEXT, R, allowed_arguments=("recipient", "day"))
        self.assertEqual(v.arguments, {"recipient": "Jane Citizen"})
        self.assertIn("argument_not_verbatim:day", v.violations)
        self.assertIn("argument_not_declared:url", v.violations)

    def test_confidence_is_clamped(self):
        self.assertEqual(validate({"confidence": 7}, TEXT, R).confidence, 1.0)
        self.assertEqual(validate({"confidence": -2}, TEXT, R).confidence, 0.0)
        self.assertIn("bad_confidence", validate({"confidence": "high"}, TEXT, R).violations)

    def test_malformed_output_is_safe(self):
        for bad in (None, "email.send", ["email.send"], 42):
            v = validate(bad, TEXT, R)
            self.assertEqual(v.candidates, [])
            self.assertIn("not_an_object", v.violations)

    def test_wrong_types_at_every_level_never_raise(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("no string for you")

            __repr__ = __str__

        deep = []
        for _ in range(100_000):
            deep = [deep]
        junk = (None, True, 5, 2.5, 10 ** 400, float("nan"), float("inf"), "text", b"bytes", [], [1, [2]], {},
                {"a": {"b": []}}, (1, 2), {1, 2}, object(), Hostile(), deep)
        for value in junk:
            for field in ("candidates", "intent", "confidence", "arguments"):
                v = validate({field: value}, TEXT, R, allowed_arguments=("recipient",))
                self.assertEqual(v.arguments, {}, (field, value.__class__))
                self.assertTrue(set(v.candidates) <= {"calendar.read", "email.send"})
                self.assertTrue(0.0 <= v.confidence <= 1.0, (field, value.__class__))
            validate({"candidates": [value, "email.send"], "arguments": {"recipient": value}}, TEXT, R,
                     allowed_arguments=("recipient",))
            validate({"intent": "email.send"}, value, R)                      # the user's text is the wrong type
            validate(value, TEXT, R)
            validate({value if isinstance(value, (str, int, float, bool, tuple, type(None))) else "k": 1}, TEXT, R)

    def test_candidates_that_are_not_a_list_are_reported_and_dropped(self):
        for bad in (5, True, "email.send", {"email.send": 1}, 2.5):
            v = validate({"candidates": bad}, TEXT, R)
            self.assertEqual(v.candidates, [], bad)
            self.assertIn("candidates_not_a_list", v.violations, bad)
        v = validate({"candidates": 5, "intent": "email.send"}, TEXT, R)       # the intent still stands
        self.assertEqual(v.candidates, ["email.send"])

    def test_bad_confidence_is_zero_and_reported(self):
        for bad in (10 ** 400, float("nan"), "high", [1], {}):
            v = validate({"confidence": bad}, TEXT, R)
            self.assertEqual(v.confidence, 0.0, bad)
            self.assertIn("bad_confidence", v.violations, bad)
        self.assertEqual(validate({"confidence": float("inf")}, TEXT, R).confidence, 1.0)

    def test_argument_values_must_be_plain_text_or_numbers(self):
        v = validate({"intent": "email.send", "arguments": {"recipient": ["Jane Citizen"], "day": {"x": "Tuesday"}}},
                     TEXT, R, allowed_arguments=("recipient", "day"))
        self.assertEqual(v.arguments, {})
        self.assertIn("argument_not_text:recipient", v.violations)
        self.assertIn("argument_not_text:day", v.violations)

    def test_forbidden_field_with_spaces_or_capitals_is_reported(self):
        v = validate({" Approve ": True, "PERMISSIONS": []}, TEXT, R)
        self.assertEqual(len([x for x in v.violations if x.startswith("forbidden_field:")]), 2)

    def test_an_internal_error_gives_the_empty_result(self):
        class Broken:
            def __contains__(self, name):
                raise RuntimeError("registry is broken")
        v = validate({"intent": "email.send", "arguments": {"recipient": "Jane Citizen"}}, TEXT, Broken(),
                     allowed_arguments=("recipient",))
        self.assertEqual((v.candidates, v.arguments, v.confidence, v.violations), ([], {}, 0.0, ["validator_error"]))

    def test_zero_candidates_allowed_means_none(self):
        self.assertEqual(validate({"candidates": ["calendar.read"]}, TEXT, R, max_candidates=0).candidates, [])

    def test_candidate_cap(self):
        v = validate({"candidates": ["calendar.read", "email.send"]}, TEXT, R, max_candidates=1)
        self.assertEqual(v.candidates, ["calendar.read"])

    def test_unknown_intent_is_ignored(self):
        v = validate({"intent": "unknown"}, TEXT, R)
        self.assertEqual(v.candidates, [])
        self.assertTrue(v.clean)


if __name__ == "__main__":
    unittest.main()
