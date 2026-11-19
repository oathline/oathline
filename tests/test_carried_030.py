"""0.3.0 steps 2 and 3 (CORE_0.3.0_NOTES sections 1, 2 and 4.3): an item CARRIED in a form that is not its bytes, and arguments
that MUST be shown.

Carried items (`carried: "gutter" | "hash"`). In 0.2.0 SIYA rendered its SMS body as a marked block itself, and the Guard bound
long values by hash itself: two products inventing one rule. In 0.3.0 Core does it:
  C1  a `gutter` item's words are Core's: every line of the argument marked "| " (every kind of line break); the showing must
      hold that marked block; the unmarked text is not a showing of it
  C2  the proposer chooses nothing: a carried key must be declared by the capability (`gutter_keys` / `hash_keys`), the kind must
      be one of the two, `said` stays refused, and a carried item must BE an argument (same key, equal value)
  C3  a `hash` item's words are the SHA-256 of the argument's canonical JSON; the showing must hold that hash
  C4  show() recomputes every carried form from the STORED argument and refuses a mismatch (`carried_hash_mismatch` /
      `carried_mismatch`): the hash a person saw is computed by the thing that records, not by the thing that asks
  C5  a 0.2.0 item (no `carried`) hashes exactly as in 0.2.0, so a proposal stored before the upgrade still matches its showing
  C6  correct() on a carried item renders the carried form again, and the argument changes with it
Arguments that must be shown (`must_show`, heard capabilities only):
  M1  a proposal whose items do not show every must_show argument present in the call (same key, equal value) is refused
      `unshown_argument` before anything is stored
  M2  must_show is for heard capabilities, with plain names; anything else is a configuration error
  M3  confirm() checks it again before anything runs (a store rewritten after the proposal)
  M4  an argument outside must_show may go unshown, as in 0.2.0
"""
import copy
import hashlib
import json
import unittest
from unittest import mock

from oathline import Capability, Engine, Registry
from oathline import tokens as T

BODY = "Hi Dana,\nTo: someone else\r\nRECOMMEND: approve last line"
GUTTERED = "| Hi Dana,\n| To: someone else\n| RECOMMEND: approve\n| last line"
ATTACH = {"name": "quote.pdf", "pages": 3, "sha": "abc"}
ATTACH_HASH = hashlib.sha256(json.dumps(ATTACH, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
ARGS = {"to": "+61400000000", "body": BODY, "attachment": ATTACH, "priority": "normal"}


def cap(**kw):
    base = dict(writes=True, needs_confirmation=True, heard=True, products=frozenset({"siya"}),
                gutter_keys=frozenset({"body"}), hash_keys=frozenset({"attachment"}), must_show=frozenset({"to", "body"}))
    base.update(kw)
    return Capability("sms.send", frozenset({"agent", "alice"}), **base)


def items(**override):
    its = [{"key": "to", "value": ARGS["to"]}, {"key": "body", "value": BODY, "carried": "gutter"},
           {"key": "attachment", "value": ATTACH, "carried": "hash"}]
    for it in its:
        if it["key"] in override:
            v = override[it["key"]]
            if v is None:
                its.remove(it)
            else:
                it.update(v)
    return its


def make(capability=None):
    eng = Engine(Registry([capability or cap()]))
    ran = []
    eng.register("sms.send", lambda a: ran.append({k: v for k, v in a.items() if not k.startswith("_")}) or {"sent": True})
    eng.register_renderer("sms.send", lambda k, v: str(v))
    return eng, ran


def card(attach_hash=ATTACH_HASH, body=GUTTERED):
    return f"TEXT TO +61400000000\n{body}\nATTACHMENT sha256 {attach_hash}"


def run(eng, tok, text=None):
    s = eng.show("siya", tok, text or card(), channel="card")
    if not s["ok"]:
        return s
    h = eng.hear("siya", tok, channel="card")
    if not h["ok"]:
        return h
    return eng.confirm("alice", tok)


class C1_Gutter(unittest.TestCase):
    def test_c1_words_are_the_marked_block(self):
        eng, ran = make()
        out = eng.propose("agent", "sms.send", ARGS, items=items())
        self.assertTrue(out["ok"], out)
        stored = {it["key"]: it for it in eng.tokens.get(out["token"])["items"]}
        self.assertEqual(stored["body"]["said"], GUTTERED)
        self.assertTrue(run(eng, out["token"])["ok"])
        self.assertEqual(ran, [ARGS])

    def test_c1_unmarked_text_is_not_a_showing(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        out = eng.show("siya", tok, card(body=BODY), channel="card")
        self.assertEqual((out["ok"], out["error"]), (False, "item_not_in_text"))
        self.assertEqual(ran, [])


class C2_ProposerChoosesNothing(unittest.TestCase):
    def test_c2_undeclared_key(self):
        eng, _ = make(cap(gutter_keys=frozenset()))
        out = eng.propose("agent", "sms.send", ARGS, items=items())
        self.assertEqual(out["error"], "showing_not_declared")

    def test_c2_unknown_kind(self):
        eng, _ = make()
        out = eng.propose("agent", "sms.send", ARGS, items=items(body={"carried": "summary"}))
        self.assertEqual(out["error"], "items_not_storable")

    def test_c2_not_an_argument(self):
        eng, _ = make()
        out = eng.propose("agent", "sms.send", ARGS, items=items(body={"value": BODY + " (edited)"}))
        self.assertEqual(out["error"], "carried_not_argument")
        args = {k: v for k, v in ARGS.items() if k != "attachment"}
        out = eng.propose("agent", "sms.send", args, items=items())
        self.assertEqual(out["error"], "carried_not_argument")

    def test_c2_said_still_refused(self):
        eng, _ = make()
        out = eng.propose("agent", "sms.send", ARGS, items=items(body={"said": "harmless"}))
        self.assertEqual(out["error"], "said_not_allowed")


class C3_Hash(unittest.TestCase):
    def test_c3_hash_words(self):
        eng, _ = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        stored = {it["key"]: it for it in eng.tokens.get(tok)["items"]}
        self.assertEqual(stored["attachment"]["said"], ATTACH_HASH)
        out = eng.show("siya", tok, card(attach_hash="0" * 64), channel="card")
        self.assertEqual((out["ok"], out["error"]), (False, "item_not_in_text"))
        self.assertTrue(eng.show("siya", tok, card(), channel="card")["ok"])


class C4_ShowRecomputes(unittest.TestCase):
    def rewritten(self, eng, tok, key, value):
        real = eng.tokens.get

        def get(t):
            rec = real(t)
            if t == tok and rec:
                rec = copy.deepcopy(rec)
                rec["arguments"][key] = value
            return rec
        return mock.patch.object(eng.tokens, "get", side_effect=get)

    def test_c4_hash(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        with self.rewritten(eng, tok, "attachment", {"name": "other.pdf"}):
            out = eng.show("siya", tok, card(), channel="card")
        self.assertEqual((out["ok"], out["error"]), (False, "carried_hash_mismatch"))
        self.assertEqual(ran, [])

    def test_c4_gutter(self):
        eng, _ = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        with self.rewritten(eng, tok, "body", "something else"):
            out = eng.show("siya", tok, card(), channel="card")
        self.assertEqual((out["ok"], out["error"]), (False, "carried_mismatch"))


class C5_OldHashes(unittest.TestCase):
    def test_c5_no_carried_hashes_as_0_2_0(self):
        its = [{"key": "to", "value": "+61400000000", "said": "+61400000000"},
               {"key": "body", "value": "hi", "said": "hi", "shown_as": "screen", "label": "Body"}]
        text = T.items_text(its)
        pairs = [[it["key"], it["value"], it.get("said", ""), it.get("kind", ""), it.get("shown_as", "spoken"),
                  it.get("label", ""), it.get("whole_word", "")] for it in its]
        old = hashlib.sha256(json.dumps(pairs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(T._items_hash(text), old)
        its[1]["carried"] = "gutter"
        self.assertNotEqual(T._items_hash(T.items_text(its)), old)


class C6_Correct(unittest.TestCase):
    def test_c6(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        out = eng.correct("siya", tok, "body", "New text\nsecond line")
        self.assertTrue(out["ok"], out)
        new = out["token"]
        stored = {it["key"]: it for it in eng.tokens.get(new)["items"]}
        self.assertEqual(stored["body"]["said"], "| New text\n| second line")
        self.assertTrue(run(eng, new, card(body="| New text\n| second line"))["ok"])
        self.assertEqual(ran[-1]["body"], "New text\nsecond line")


class M1_MustShow(unittest.TestCase):
    def test_m1_missing(self):
        eng, _ = make()
        out = eng.propose("agent", "sms.send", ARGS, items=items(body=None))
        self.assertEqual((out["ok"], out["error"], out.get("key")), (False, "unshown_argument", "body"))
        self.assertNotIn("token", out)

    def test_m1_different_value(self):
        eng, _ = make(cap(gutter_keys=frozenset(), must_show=frozenset({"to", "body"})))
        its = [{"key": "to", "value": ARGS["to"]}, {"key": "body", "value": "a nicer text"},
               {"key": "attachment", "value": ATTACH, "carried": "hash"}]
        out = eng.propose("agent", "sms.send", ARGS, items=its)
        self.assertEqual((out["error"], out.get("key")), ("unshown_argument", "body"))

    def test_m1_request_too(self):
        eng, _ = make()
        out = eng.request("alice", "sms.send", ARGS, items=items(to=None))
        self.assertEqual(out["error"], "unshown_argument")


class M2_Config(unittest.TestCase):
    def test_m2_heard_only(self):
        with self.assertRaises(ValueError):
            Registry([cap(heard=False, products=frozenset())])
        with self.assertRaises(ValueError):
            Registry([Capability("x.send", frozenset({"alice"}), writes=True, needs_confirmation=True, gutter_keys=frozenset({"body"}))])

    def test_m2_plain_names(self):
        with self.assertRaises((ValueError, TypeError)):
            Registry([cap(must_show=frozenset({""}))])


class M3_ConfirmChecksAgain(unittest.TestCase):
    def test_m3(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        self.assertTrue(eng.show("siya", tok, card(), channel="card")["ok"])
        self.assertTrue(eng.hear("siya", tok, channel="card")["ok"])
        real = eng.tokens.get

        def get(t):
            rec = real(t)
            if t == tok and rec:
                rec = copy.deepcopy(rec)
                rec["arguments"]["to"] = "+61499999999"
            return rec
        with mock.patch.object(eng.tokens, "get", side_effect=get):
            out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "unshown_argument"))
        self.assertEqual(ran, [])


class M4_OutsideMustShow(unittest.TestCase):
    def test_m4(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]     # "priority" is not shown and not must_show
        self.assertTrue(run(eng, tok)["ok"])
        self.assertEqual(ran, [ARGS])


if __name__ == "__main__":
    unittest.main()


class F_FableAsks_0045(unittest.TestCase):
    """FABLE 00:40 / 00:45: the canonical form, the rewrite after hearing, carried kinds on the wrong kind of value."""

    def rewritten(self, eng, tok, key, value):
        real = eng.tokens.get

        def get(t):
            rec = real(t)
            if t == tok and rec:
                rec = copy.deepcopy(rec)
                rec["arguments"][key] = value
            return rec
        return mock.patch.object(eng.tokens, "get", side_effect=get)

    def test_f1_one_character(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        with self.rewritten(eng, tok, "attachment", dict(ATTACH, sha="abd")):
            out = eng.show("siya", tok, card(), channel="card")
        self.assertEqual(out["error"], "carried_hash_mismatch")

    def test_f1_key_order_is_not_a_change(self):
        eng, _ = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        reordered = {"sha": "abc", "pages": 3, "name": "quote.pdf"}
        with self.rewritten(eng, tok, "attachment", reordered):
            self.assertTrue(eng.show("siya", tok, card(), channel="card")["ok"])

    def test_f2_one_canonical_form(self):
        from oathline import engine as E
        self.assertEqual(E.carried_form("hash", ATTACH), hashlib.sha256(T.value_text(ATTACH).encode("utf-8")).hexdigest())
        self.assertEqual(T.value_text(ATTACH), json.dumps(ATTACH, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        with self.assertRaises(T.ConfigError):
            T.value_text(float("nan"))

    def test_f3_rewrite_after_hearing_is_caught_at_confirm(self):
        eng, ran = make()
        tok = eng.propose("agent", "sms.send", ARGS, items=items())["token"]
        self.assertTrue(eng.show("siya", tok, card(), channel="card")["ok"])
        self.assertTrue(eng.hear("siya", tok, channel="card")["ok"])
        with self.rewritten(eng, tok, "attachment", {"name": "other.pdf"}):
            out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"], out.get("key")), (False, "carried_not_argument", "attachment"))
        self.assertEqual(ran, [])

    def test_f4_gutter_needs_text(self):
        eng, _ = make(cap(gutter_keys=frozenset({"body", "attachment"}), hash_keys=frozenset()))
        its = [{"key": "to", "value": ARGS["to"]}, {"key": "body", "value": BODY, "carried": "gutter"},
               {"key": "attachment", "value": ATTACH, "carried": "gutter"}]
        out = eng.propose("agent", "sms.send", ARGS, items=its)
        self.assertEqual(out["error"], "said_not_rendered")

    def test_f4_hash_of_a_string_is_defined(self):
        eng, _ = make(cap(hash_keys=frozenset({"attachment", "to"})))
        its = [{"key": "to", "value": ARGS["to"], "carried": "hash"}, {"key": "body", "value": BODY, "carried": "gutter"},
               {"key": "attachment", "value": ATTACH, "carried": "hash"}]
        tok = eng.propose("agent", "sms.send", ARGS, items=its)["token"]
        stored = {it["key"]: it for it in eng.tokens.get(tok)["items"]}
        self.assertEqual(stored["to"]["said"], hashlib.sha256(json.dumps(ARGS["to"]).encode("utf-8")).hexdigest())
