"""The hearing rule (0.2.0): nothing is written unheard. A capability that declares `heard=True` runs only after the
human was SHOWN exactly what will be written, HEARD it, and said yes to the items as they stand at that moment; every
step is on the record. A capability that does not declare it behaves exactly as in 0.1.2."""
import hashlib
import unittest

from oathline import Capability, Engine, Registry, TokenStore


def gated(name="email.send", **kw):
    return Capability(name, frozenset({"agent", "alice"}), writes=True, needs_confirmation=True, **kw)


ITEMS = [{"key": "to", "value": "bob@example.com", "said": "bob at example dot com", "label": "To"},
         {"key": "amount", "value": 2400000, "said": "two point four million", "label": "Amount"}]
TEXT = "I'll pay bob at example dot com two point four million. Right?"


def make(**engine_kw):
    """pay.send is a HEARD capability whose product actor is siya; email.send is gated as in 0.1.2; notes.read is a read."""
    reg = Registry([gated("pay.send", heard=True, products=frozenset({"siya"})), gated("email.send", products=frozenset({"siya"})),
                    Capability("notes.read", frozenset({"agent", "alice"}))])
    eng = Engine(reg, **engine_kw)
    ran = []
    eng.register("pay.send", lambda a: ran.append(("pay.send", a)) or {"paid": a.get("to")})
    eng.register("email.send", lambda a: ran.append(("email.send", a)) or {"sent_to": a.get("to")})
    eng.register("notes.read", lambda a: {"notes": []})
    return eng, ran


def events(eng, kind=None):
    return [e for e in eng.audit.events() if kind is None or e["event_type"] == kind]


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HeardFlagTest(unittest.TestCase):
    def test_heard_is_off_by_default(self):
        self.assertFalse(gated().heard)
        self.assertFalse(Capability("notes.read", frozenset({"alice"})).heard)

    def test_a_heard_capability_must_be_gated(self):
        with self.assertRaises(ValueError):
            Registry([Capability("notes.read", frozenset({"alice"}), heard=True)])

    def test_a_gated_capability_may_declare_heard(self):
        reg = Registry([gated(heard=True, products=frozenset({"siya"}))])
        self.assertTrue(reg.get("email.send").heard)
        self.assertTrue(reg.get("email.send").gated)


class ItemsOnTheEngineTest(unittest.TestCase):
    def test_a_heard_capability_needs_items(self):
        eng, ran = make()
        for call in (eng.request, eng.propose):
            out = call("agent", "pay.send", {"to": "bob@example.com"})
            self.assertEqual((out["ok"], out["error"]), (False, "items_required"))
            self.assertNotIn("token", out)
        self.assertEqual(ran, [])
        self.assertEqual([e["event_type"] for e in events(eng)][-1], "REQUEST_REFUSED")

    def test_a_capability_of_0_1_2_is_untouched_by_items(self):
        eng, ran = make()
        plain = eng.request("alice", "email.send", {"to": "bob@example.com"})
        with_items = eng.request("alice", "email.send", {"to": "bob@example.com"}, items=ITEMS)
        self.assertEqual((plain["state"], with_items["state"]), ("proposed", "proposed"))
        self.assertIsNone(events(eng, "PROPOSED")[0]["payload"]["items_hash"])
        self.assertTrue(events(eng, "PROPOSED")[1]["payload"]["items_hash"])
        self.assertTrue(eng.confirm("alice", plain["token"])["ok"])          # no hearing asked of it
        self.assertTrue(eng.confirm("alice", with_items["token"])["ok"])
        self.assertEqual(len(ran), 2)

    def test_bad_items_are_refused_before_anything_is_stored(self):
        eng, _ = make()
        self.assertEqual(eng.request("agent", "pay.send", {}, items="to=bob")["error"], "items_not_storable")
        big = [{"key": "notes", "value": "x" * 9000}]
        self.assertEqual(eng.request("agent", "pay.send", {}, items=big)["error"], "items_too_large")
        self.assertEqual(events(eng, "PROPOSED"), [])


class ShownTest(unittest.TestCase):
    def proposal(self, eng, items=ITEMS, capability="pay.send"):
        return eng.request("agent", capability, {"to": "bob@example.com"}, items=items)["token"]

    def test_the_default_log_holds_no_shown_text(self):
        eng, _ = make()
        token = self.proposal(eng)
        out = eng.show("siya", token, TEXT, channel="spoken")
        self.assertEqual((out["ok"], out["state"]), (True, "shown"))
        self.assertEqual(out["text_sha256"], sha_text(TEXT))
        shown = events(eng, "SHOWN")
        self.assertEqual(len(shown), 1)
        payload = shown[0]["payload"]
        self.assertEqual((payload["token"], payload["text_sha256"], payload["chars"], payload["channel"]),
                         (token, sha_text(TEXT), len(TEXT), "spoken"))
        self.assertEqual(payload["items_hash"], out["items_hash"])
        self.assertNotIn("text", payload)
        self.assertNotIn("text_preview", payload)
        self.assertNotIn("bob at example", str(eng.audit.events()))      # nowhere in the log, under any key

    def test_record_shown_text_keeps_the_text_bounded(self):
        eng, _ = make(record_shown_text=True)
        token = self.proposal(eng)
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        self.assertEqual(events(eng, "SHOWN")[0]["payload"]["text"], TEXT)
        long_items = [{"key": "notes", "value": "slab poured", "said": "slab poured"}]
        token2 = self.proposal(eng, items=long_items)
        long_text = "slab poured. " + "and more words " * 400 + "Right?"
        self.assertGreater(len(long_text), 4096)
        self.assertTrue(eng.show("siya", token2, long_text, channel="spoken")["ok"])
        payload = events(eng, "SHOWN")[1]["payload"]
        self.assertNotIn("text", payload)
        self.assertEqual(payload["text_preview"], long_text[:512])
        self.assertEqual(payload["chars"], len(long_text))

    def test_the_said_form_must_be_in_the_text(self):
        eng, _ = make()
        token = self.proposal(eng)
        out = eng.show("siya", token, "I'll pay bob at example dot com 2400000. Right?", channel="spoken")
        self.assertEqual((out["ok"], out["error"], out["key"]), (False, "item_not_in_text", "amount"))
        self.assertEqual(events(eng, "SHOWN"), [])
        self.assertEqual(events(eng, "SHOW_REFUSED")[-1]["payload"]["reason"], "item_not_in_text")
        bare = [{"key": "to", "value": "bob@example.com"}]                 # no said: the value itself must be in the text
        token2 = self.proposal(eng, items=bare)
        self.assertEqual(eng.show("siya", token2, "I'll pay bob. Right?", channel="spoken")["error"], "item_not_in_text")
        self.assertTrue(eng.show("siya", token2, "I'll pay bob@example.com. Right?", channel="spoken")["ok"])

    def test_a_mark_not_named_is_not_shown(self):
        eng, _ = make()
        crew = [{"key": "people", "value": ["Kerry Walsh", "Tom Nguyen", "Sarah Mitchell"], "kind": "list", "label": "Attendance"}]
        token = self.proposal(eng, items=crew)
        out = eng.show("siya", token, "I'll tick Kerry Walsh and Tom Nguyen. Right?", channel="spoken")
        self.assertEqual((out["ok"], out["error"], out["key"]), (False, "unshown_mark", "people"))
        self.assertTrue(eng.show("siya", token, "I'll tick Kerry Walsh, Tom Nguyen and Sarah Mitchell. Right?", channel="spoken")["ok"])

    def test_everyone_names_them_all(self):
        eng, _ = make()
        crew = [{"key": "people", "value": ["Kerry Walsh", "Tom Nguyen", "Sarah Mitchell"], "kind": "list", "whole_word": "everyone"}]
        token = self.proposal(eng, items=crew)
        self.assertTrue(eng.show("siya", token, "I'll tick everyone. Right?", channel="spoken")["ok"])
        self.assertEqual(eng.show("siya", token, "I'll tick the lot. Right?", channel="spoken")["error"], "unshown_mark")

    def test_screen_items_need_the_screen(self):
        eng, _ = make()
        email = [{"key": "body", "value": "Dear Bob, " * 40, "shown_as": "screen", "label": "the email"}]
        token = self.proposal(eng, items=email)
        self.assertEqual(eng.show("siya", token, "I'll send the email as typed. Right?", channel="spoken")["error"], "screen_not_allowed")
        self.assertEqual(eng.show("siya", token, "I'll send it. Right?", channel="screen")["error"], "screen_not_allowed")
        out = eng.show("siya", token, "I'll send the email as typed, check it on screen. Right?", channel="screen")
        self.assertTrue(out["ok"])
        self.assertEqual(events(eng, "SHOWN")[0]["payload"]["channel"], "screen")

    def test_a_preset_is_shown_like_a_spoken_value(self):
        eng, _ = make()
        hours = [{"key": "hours", "value": 8, "shown_as": "preset", "label": "Hours worked"}]
        token = self.proposal(eng, items=hours)
        self.assertEqual(eng.show("siya", token, "I'll save the diary. Right?", channel="spoken")["error"], "item_not_in_text")
        self.assertTrue(eng.show("siya", token, "Hours worked: 8, the form's own. Right?", channel="spoken")["ok"])

    def test_text_too_large_shows_nothing(self):
        eng, _ = make()
        token = self.proposal(eng)
        out = eng.show("siya", token, TEXT + " x" * 600_000, channel="spoken")
        self.assertEqual(out["error"], "text_too_large")
        self.assertEqual(events(eng, "SHOWN"), [])
        for bad in ("", None, 7):
            self.assertEqual(eng.show("siya", token, bad, channel="spoken")["error"], "bad_text")
        self.assertEqual(eng.show("siya", token, TEXT, channel="telepathy")["error"], "bad_channel")

    def test_show_needs_a_pending_token_with_items(self):
        eng, _ = make()
        self.assertEqual(eng.show("siya", "tok_" + "0" * 32, TEXT, channel="spoken")["error"], "unknown_token")
        self.assertEqual(eng.show("siya", "not a token", TEXT, channel="spoken")["error"], "unknown_token")
        self.assertEqual(eng.show("", "tok_" + "0" * 32, TEXT, channel="spoken")["error"], "bad_principal")
        plain = eng.request("alice", "email.send", {"to": "bob@example.com"})["token"]
        self.assertEqual(eng.show("siya", plain, TEXT, channel="spoken")["error"], "items_required")
        with_items = eng.request("alice", "email.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertTrue(eng.confirm("alice", with_items)["ok"])
        self.assertEqual(eng.show("siya", with_items, TEXT, channel="spoken")["error"], "already_used")


class HeardTest(unittest.TestCase):
    def heard_token(self, eng, items=ITEMS, text=TEXT, capability="pay.send"):
        token = eng.request("agent", capability, {"to": "bob@example.com"}, items=items)["token"]
        self.assertTrue(eng.show("siya", token, text, channel="spoken")["ok"])
        out = eng.hear("siya", token, channel="spoken")
        self.assertEqual((out["ok"], out["state"]), (True, "heard"), out)
        return token, out

    def test_confirm_without_a_showing_is_unheard(self):
        eng, ran = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        out = eng.confirm("alice", token)
        self.assertEqual((out["ok"], out["error"]), (False, "unheard"))
        self.assertEqual(ran, [])
        self.assertEqual(events(eng, "CONFIRM_REFUSED")[-1]["payload"]["reason"], "unheard")
        self.assertEqual(eng.tokens.get(token)["state"], "proposed")           # nothing spent

    def test_shown_but_not_heard_is_unheard(self):
        eng, ran = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        self.assertEqual(eng.confirm("alice", token)["error"], "unheard")
        self.assertEqual(ran, [])

    def test_hear_needs_a_showing_first(self):
        eng, _ = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        out = eng.hear("siya", token, channel="spoken")
        self.assertEqual(out["error"], "not_shown")
        self.assertEqual(events(eng, "HEAR_REFUSED")[-1]["payload"]["reason"], "not_shown")
        self.assertEqual(eng.hear("siya", token, channel="telepathy")["error"], "bad_channel")

    def test_heard_then_confirmed_runs_once_and_records_the_hearing(self):
        eng, ran = make()
        token, heard = self.heard_token(eng)
        h = events(eng, "HEARD")[-1]
        shown = events(eng, "SHOWN")[-1]
        self.assertEqual(h["payload"], {"token": token, "items_hash": heard["items_hash"], "text_sha256": sha_text(TEXT),
                                        "channel": "spoken", "shown_seq": shown["seq"]})
        self.assertEqual(heard["heard_seq"], h["seq"])
        out = eng.confirm("alice", token)
        self.assertEqual(out["state"], "executed", out)
        self.assertEqual(len(ran), 1)
        confirmed = events(eng, "CONFIRMED_BY_HUMAN")[-1]["payload"]
        self.assertEqual((confirmed["items_hash"], confirmed["heard_seq"], confirmed["text_sha256"]),
                         (heard["items_hash"], h["seq"], sha_text(TEXT)))
        self.assertEqual(events(eng, "EXECUTING")[-1]["payload"]["items_hash"], heard["items_hash"])
        self.assertEqual(eng.confirm("alice", token)["error"], "already_used")
        self.assertEqual(eng.hear("siya", token, channel="spoken")["error"], "already_used")
        self.assertTrue(eng.audit.verify()["ok"])

    def test_a_second_showing_of_the_same_items_is_heard_as_the_latest(self):
        eng, _ = make()
        token, _ = self.heard_token(eng)
        other = "Paying bob at example dot com two point four million now. Right?"
        self.assertTrue(eng.show("siya", token, other, channel="spoken")["ok"])
        self.assertTrue(eng.hear("siya", token, channel="spoken")["ok"])
        self.assertTrue(eng.confirm("alice", token)["ok"])
        self.assertEqual(events(eng, "CONFIRMED_BY_HUMAN")[-1]["payload"]["text_sha256"], sha_text(other))

    def test_a_hearing_belongs_to_one_token(self):
        eng, ran = make()
        self.heard_token(eng)
        twin = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]   # identical items
        self.assertEqual(eng.confirm("alice", twin)["error"], "unheard")
        self.assertEqual(len(ran), 0)

    def test_an_agent_hearing_grants_nothing(self):
        eng, ran = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])      # the product shows and hears
        self.assertTrue(eng.hear("siya", token, channel="spoken")["ok"])
        self.assertEqual(eng.confirm("agent", token)["error"], "non_human_principal")   # the agent still cannot confirm
        self.assertEqual(eng.confirm("siya", token)["error"], "not_an_approver")      # nor can the product
        self.assertEqual(ran, [])
        self.assertEqual({e["principal"] for e in events(eng, "SHOWN") + events(eng, "HEARD")}, {"siya"})

    def test_rewritten_items_and_hash_are_a_stale_hearing(self):
        eng, ran = make()
        token, _ = self.heard_token(eng)
        new_items = [dict(ITEMS[0]), {"key": "amount", "value": 9900000, "said": "nine point nine million", "label": "Amount"}]
        twin = eng.tokens.propose("agent", "pay.send", {"to": "bob@example.com"}, items=new_items)
        rewritten = eng.tokens.get(twin)
        eng.tokens._db.execute("UPDATE proposals SET items=(SELECT items FROM proposals WHERE id=?),"
                               " items_hash=? WHERE id=?", (twin, rewritten["items_hash"], token))
        eng.tokens._db.commit()
        self.assertEqual(eng.confirm("alice", token)["error"], "stale_hearing")
        self.assertEqual(ran, [])

    def test_rewritten_items_alone_are_tampered(self):
        eng, ran = make()
        token, _ = self.heard_token(eng)
        eng.tokens._db.execute("UPDATE proposals SET items=? WHERE id=?", ('[{"key": "to", "value": "mallory@example.com"}]', token))
        eng.tokens._db.commit()
        self.assertEqual(eng.confirm("alice", token)["error"], "tampered")
        self.assertEqual(ran, [])

    def test_a_showing_of_rewritten_items_is_stale(self):
        eng, _ = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        new_items = [dict(ITEMS[0]), {"key": "amount", "value": 9900000, "said": "nine point nine million", "label": "Amount"}]
        twin = eng.tokens.propose("agent", "pay.send", {"to": "bob@example.com"}, items=new_items)
        eng.tokens._db.execute("UPDATE proposals SET items=(SELECT items FROM proposals WHERE id=?), items_hash=? WHERE id=?",
                               (twin, eng.tokens.get(twin)["items_hash"], token))
        eng.tokens._db.commit()
        self.assertEqual(eng.hear("siya", token, channel="spoken")["error"], "stale_showing")

    def test_why_says_the_last_word(self):
        eng, _ = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        eng.confirm("alice", token)
        self.assertIn("unheard", eng.why(token))
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        self.assertTrue(eng.hear("siya", token, channel="spoken")["ok"])
        self.assertTrue(eng.confirm("alice", token)["ok"])
        self.assertIn("executed", eng.why(token).lower())
        self.assertEqual(eng.why("tok_" + "0" * 32), "no such token")

    def test_unheard_runs_lists_a_run_that_had_no_hearing(self):
        eng, _ = make()
        token = eng.request("alice", "email.send", {"to": "bob@example.com"}, items=ITEMS)["token"]   # not a heard capability
        self.assertTrue(eng.confirm("alice", token)["ok"])                                           # ran, as in 0.1.2
        self.assertEqual(eng.unheard_runs(), [])
        reg = Registry([gated("email.send", heard=True, products=frozenset({"siya"})), gated("pay.send", heard=True, products=frozenset({"siya"}))])
        later = Engine(reg, audit=eng.audit, tokens=eng.tokens)                                       # the same log, the rule now on
        later.register("pay.send", lambda a: {"paid": True})
        listed = later.unheard_runs()
        self.assertEqual([(r["token"], r["reason"]) for r in listed], [(token, "no_hearing")])
        heard, _ = self.heard_token(later)
        self.assertTrue(later.confirm("alice", heard)["ok"])
        self.assertEqual([r["token"] for r in later.unheard_runs()], [token])                        # the heard run is not listed


class CorrectTest(unittest.TestCase):
    def heard(self, eng):
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        self.assertTrue(eng.hear("siya", token, channel="spoken")["ok"])
        return token

    def test_a_correction_is_a_new_token_and_the_old_one_is_withdrawn(self):
        eng, ran = make()
        old = self.heard(eng)
        out = eng.correct("siya", old, "amount", 2500000, said="two point five million")
        self.assertEqual((out["ok"], out["state"]), (True, "proposed"), out)
        new = out["token"]
        self.assertNotEqual(new, old)
        self.assertEqual(out["corrected_from"], old)
        self.assertEqual(eng.tokens.get(old)["state"], "expired")
        withdrawn = events(eng, "WITHDRAWN")[-1]["payload"]
        self.assertEqual((withdrawn["token"], withdrawn["reason"], withdrawn["new_token"]), (old, "corrected", new))
        proposed = events(eng, "PROPOSED_BY_AGENT")[-1]["payload"]
        self.assertEqual((proposed["token"], proposed["corrected_from"], proposed["items_hash"]), (new, old, out["items_hash"]))
        self.assertEqual(eng.confirm("alice", old)["error"], "expired")
        self.assertEqual(eng.confirm("alice", new)["error"], "unheard")                  # the new items must be shown and heard
        self.assertEqual(ran, [])
        text = "I'll pay bob at example dot com two point five million. Right?"
        self.assertEqual(eng.show("siya", new, TEXT, channel="spoken")["error"], "item_not_in_text")   # the old words no longer fit
        self.assertTrue(eng.show("siya", new, text, channel="spoken")["ok"])
        self.assertTrue(eng.hear("siya", new, channel="spoken")["ok"])
        self.assertEqual(eng.confirm("alice", new)["state"], "executed")
        self.assertEqual(len(ran), 1)
        self.assertEqual(events(eng, "EXECUTING")[-1]["payload"]["items_hash"], out["items_hash"])
        self.assertTrue(eng.audit.verify()["ok"])

    def test_a_correction_keeps_the_other_items_and_the_arguments(self):
        eng, _ = make()
        old = self.heard(eng)
        new = eng.correct("siya", old, "amount", 2500000, said="two point five million")["token"]
        o, n = eng.tokens.get(old), eng.tokens.get(new)
        self.assertEqual((n["principal"], n["capability"], n["arguments"], n["integrity"]),
                         (o["principal"], o["capability"], o["arguments"], o["integrity"]))
        self.assertEqual(n["items"][0], ITEMS[0])
        self.assertEqual(n["items"][1], {"key": "amount", "value": 2500000, "said": "two point five million", "label": "Amount"})
        again = eng.correct("siya", new, "amount", 2600000)["token"]                     # no said given: the old said is dropped
        self.assertNotIn("said", eng.tokens.get(again)["items"][1])

    def test_a_correction_needs_a_known_item_and_a_storable_value(self):
        eng, _ = make()
        old = self.heard(eng)
        out = eng.correct("siya", old, "colour", "blue")
        self.assertEqual((out["error"], out["key"]), ("unknown_item", "colour"))
        self.assertEqual(eng.correct("siya", old, "amount", float("nan"))["error"], "items_not_storable")
        self.assertEqual(eng.correct("siya", old, "amount", "x" * 9000)["error"], "items_too_large")
        self.assertEqual(events(eng, "WITHDRAWN"), [])
        self.assertEqual(eng.tokens.get(old)["state"], "proposed")
        self.assertEqual(events(eng, "CORRECT_REFUSED")[-1]["payload"]["reason"], "items_too_large")

    def test_after_the_yes_nothing_moves(self):
        eng, _ = make()
        old = self.heard(eng)
        self.assertTrue(eng.confirm("alice", old)["ok"])
        for call in (lambda: eng.correct("siya", old, "amount", 1),
                     lambda: eng.show("siya", old, TEXT, channel="spoken"),
                     lambda: eng.hear("siya", old, channel="spoken")):
            self.assertEqual(call()["error"], "already_used")
        self.assertEqual(len(events(eng, "PROPOSED_BY_AGENT")), 1)
        self.assertEqual(eng.correct("siya", "tok_" + "0" * 32, "amount", 1)["error"], "unknown_token")
        plain = eng.request("alice", "email.send", {"to": "bob@example.com"})["token"]
        self.assertEqual(eng.correct("siya", plain, "to", "x")["error"], "items_required")


class ProductActorTest(unittest.TestCase):
    """Only the capability's registered product actor may show, hear or correct; never the model actor (Claude 22:05, hole 3)."""

    def test_a_heard_capability_names_its_product(self):
        with self.assertRaises(ValueError):
            Registry([gated("pay.send", heard=True)])                                   # nobody to show it: refused
        for bad in ("model", "assistant", "llm", "agent", "oathline"):
            with self.subTest(product=bad):
                with self.assertRaises(ValueError):
                    Registry([gated("pay.send", heard=True, products=frozenset({bad}))])   # the model actor is never the product
        reg = Registry([gated("pay.send", heard=True, products=frozenset({"siya"}))])
        self.assertEqual(reg.get("pay.send").products, frozenset({"siya"}))
        with self.assertRaises(TypeError):
            Registry([gated("pay.send", heard=True, products="siya")])

    def test_only_the_product_shows_hears_and_corrects(self):
        eng, ran = make()
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        for who in ("model", "agent", "alice", "mallory"):
            with self.subTest(who=who):
                self.assertEqual(eng.show(who, token, TEXT, channel="spoken")["error"], "not_the_product")
                self.assertEqual(eng.hear(who, token, channel="spoken")["error"], "not_the_product")
                self.assertEqual(eng.correct(who, token, "amount", 1)["error"], "not_the_product")
        self.assertEqual(events(eng, "SHOWN") + events(eng, "HEARD") + events(eng, "WITHDRAWN"), [])
        self.assertEqual([e["payload"]["reason"] for e in events(eng, "SHOW_REFUSED")], ["not_the_product"] * 4)
        self.assertEqual(eng.confirm("alice", token)["error"], "unheard")
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        self.assertTrue(eng.hear("siya", token, channel="spoken")["ok"])
        self.assertTrue(eng.confirm("alice", token)["ok"])
        self.assertEqual(len(ran), 1)

    def test_a_product_registered_for_one_capability_is_not_the_product_of_another(self):
        reg = Registry([gated("pay.send", heard=True, products=frozenset({"siya"})),
                        gated("post.publish", heard=True, products=frozenset({"press-desk"}))])
        eng = Engine(reg)
        eng.register("pay.send", lambda a: {})
        eng.register("post.publish", lambda a: {})
        token = eng.request("agent", "post.publish", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertEqual(eng.show("siya", token, TEXT, channel="spoken")["error"], "not_the_product")
        self.assertTrue(eng.show("press-desk", token, TEXT, channel="spoken")["ok"])


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class DeadProposalTest(unittest.TestCase):
    """A proposal past its lifetime is dead: it can be neither shown, heard, corrected nor confirmed (Claude 22:05, hole 4)."""

    def timed(self):
        clock = Clock()
        eng, ran = make(tokens=TokenStore(ttl_seconds=60, clock=clock))
        token = eng.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        return eng, ran, clock, token

    def test_a_timed_out_proposal_is_dead(self):
        eng, ran, clock, token = self.timed()
        clock.t += 61
        self.assertEqual(eng.show("siya", token, TEXT, channel="spoken")["error"], "expired")
        self.assertEqual(eng.tokens.get(token)["state"], "expired")                              # marked, not just refused
        self.assertEqual(eng.hear("siya", token, channel="spoken")["error"], "expired")
        self.assertEqual(eng.correct("siya", token, "amount", 1)["error"], "expired")
        self.assertEqual(eng.confirm("alice", token)["error"], "expired")
        self.assertEqual(ran, [])
        self.assertEqual(events(eng, "SHOWN") + events(eng, "HEARD") + events(eng, "WITHDRAWN"), [])
        self.assertEqual([e["payload"]["reason"] for e in events(eng, "SHOW_REFUSED")], ["expired"])

    def test_a_hearing_dies_with_its_proposal(self):
        eng, ran, clock, token = self.timed()
        self.assertTrue(eng.show("siya", token, TEXT, channel="spoken")["ok"])
        clock.t += 61
        self.assertEqual(eng.hear("siya", token, channel="spoken")["error"], "expired")
        eng2, ran2, clock2, token2 = self.timed()
        self.assertTrue(eng2.show("siya", token2, TEXT, channel="spoken")["ok"])
        self.assertTrue(eng2.hear("siya", token2, channel="spoken")["ok"])
        clock2.t += 61
        self.assertEqual(eng2.confirm("alice", token2)["error"], "expired")                     # heard in time, confirmed too late
        self.assertEqual(ran2, [])
        fresh = eng2.request("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)["token"]
        self.assertEqual(eng2.confirm("alice", fresh)["error"], "unheard")                      # the old hearing is not the new token's


if __name__ == "__main__":
    unittest.main()
