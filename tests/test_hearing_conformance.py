"""Conformance (0.2.0): a scripted product (a small "brain") takes forms through propose -> show -> hear -> confirm,
and the two counters the SiteIQ grader uses from outside (UNHEARD PRESS, UNGATED MARK) are computed from the
engine's own log instead: 0 / 0 for a product that keeps the rule, and the rule itself stops a product that does not.
The brain's own corpus run (477 cases, the first sessions, the holdouts) is the product's adoption step, not this
package's test; this is the same protocol on a small corpus, with the counters ported."""
import unittest

from oathline import Capability, Engine, Registry


def registry(heard=True):
    return Registry([Capability("form.save", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True, heard=heard,
                                products=frozenset({"siya"}))])


CORPUS = [
    # (name, items, scripted corrections before the yes: [(key, value, said)])
    ("diary", [{"key": "weather", "value": "fine and sunny", "label": "Weather"},
               {"key": "hours", "value": 8, "shown_as": "preset", "label": "Hours worked", "said": "8, the form's own"},
               {"key": "notes", "value": "slab poured on lot 2", "label": "Notes"}], []),
    ("toolbox", [{"key": "topic", "value": "ladders", "label": "Topic"},
                 {"key": "attendance", "value": ["Kerry Walsh", "Tom Nguyen"], "kind": "list", "label": "Ticked"}], []),
    ("toolbox-everyone", [{"key": "topic", "value": "silica dust", "label": "Topic"},
                          {"key": "attendance", "value": ["Kerry Walsh", "Tom Nguyen", "Sarah Mitchell"], "kind": "list",
                           "label": "Ticked", "whole_word": "everyone"}], []),
    ("incident", [{"key": "type", "value": "Near miss", "label": "Incident type"},
                  {"key": "lostTime", "value": False, "label": "Lost-time injury", "said": "no"}], []),
    ("project", [{"key": "name", "value": "Harbour View Townhouses", "label": "Project name"},
                 {"key": "value", "value": 2800000, "label": "Contract value", "said": "two point eight million"}], []),
    ("email", [{"key": "to", "value": "bob@example.com", "label": "To", "said": "bob at example dot com"},
               {"key": "body", "value": "Dear Bob, " * 30, "shown_as": "screen", "label": "the email"}], []),
    ("corrected", [{"key": "weather", "value": "overcast", "label": "Weather"},
                   {"key": "hours", "value": 7, "label": "Hours worked"}], [("weather", "fine", "fine")]),
    ("kept", [{"key": "swms", "value": "SWMS-0417", "shown_as": "kept", "label": "the SWMS from the library"},
              {"key": "signer", "value": "Mick Tran", "label": "Signed by"}], []),
]


def read_back(items):
    """What the product says: every item in words, lists element by element or by their whole word."""
    bits = []
    for it in items:
        label = it.get("label") or it["key"]
        if it.get("kind") == "list":
            names = it.get("whole_word") or " and ".join(it["value"])
            bits.append(f"{label}: {names}")
        elif it.get("shown_as") in ("screen", "kept"):
            bits.append(f"{label}, as typed, check it on screen")
        else:
            bits.append(f"{label}: {it.get('said') or it['value']}")
    return "I'll save: " + "; ".join(bits) + ". Right?"


class Product:
    """A scripted brain. `hears=False` is a product that forgets to say the text was spoken; `forgets` drops one name."""

    def __init__(self, eng, *, hears=True, forgets=False):
        self.eng, self.hears, self.forgets = eng, hears, forgets
        self.results = []

    def run(self, name, items, corrections):
        token = self.eng.request("agent", "form.save", {"form": name}, items=items)["token"]
        for key, value, said in corrections:                                      # the human corrects before the yes
            out = self.eng.correct("siya", token, key, value, said=said)
            assert out["ok"], out
            token = out["token"]
            items = self.eng.tokens.get(token)["items"]
        text = read_back(items)
        if self.forgets:
            text = text.replace(" and Tom Nguyen", "")
        channel = "screen" if any(it.get("shown_as") == "screen" for it in items) else "spoken"
        shown = self.eng.show("siya", token, text, channel=channel)
        if not shown["ok"]:
            self.results.append((name, "not shown", shown["error"]))
            return
        if self.hears:
            assert self.eng.hear("siya", token, channel=channel)["ok"]
        out = self.eng.confirm("alice", token)
        self.results.append((name, out.get("state") or "refused", out.get("error")))


def gate_holes(eng) -> dict:
    """The grader's two counters from the engine's own log. UNHEARD PRESS: a run of a heard capability with no HEARD
    of exactly its items before it. UNGATED MARK: a list item element of a run that the heard text never named
    (element by element, or by the item's whole word). Needs record_shown_text=True to read the texts."""
    events = eng.audit.events()
    shown_text = {e["seq"]: e["payload"].get("text", "") for e in events if e["event_type"] == "SHOWN"}
    heard = {}
    out = {"unheard_press": 0, "ungated_mark": 0, "unheard": [], "ungated": []}
    for e in events:
        p = e["payload"]
        if e["event_type"] == "HEARD":
            heard.setdefault(p["token"], []).append((e["seq"], p["items_hash"], shown_text.get(p["shown_seq"], "")))
        if e["event_type"] != "EXECUTING" or not p.get("token"):
            continue
        cap = eng.registry.get(e["action"])
        if cap is None or not cap.heard:
            continue
        matching = [h for seq, h, _ in heard.get(p["token"], []) if seq < e["seq"] and h == p.get("items_hash")]
        if not matching:
            out["unheard_press"] += 1
            out["unheard"].append(p["token"])
            continue
        text = [t for seq, h, t in heard[p["token"]] if seq < e["seq"] and h == p.get("items_hash")][-1]
        for it in eng.tokens.get(p["token"])["items"] or []:
            if it.get("kind") != "list":
                continue
            whole = it.get("whole_word")
            if whole and whole in text:
                continue
            for who in it["value"]:
                if who not in text:
                    out["ungated_mark"] += 1
                    out["ungated"].append(who)
    return out


class ConformanceTest(unittest.TestCase):
    def engine(self, heard=True):
        eng = Engine(registry(heard), record_shown_text=True)
        eng.register("form.save", lambda a: {"saved": a["form"]})
        return eng

    def test_a_product_that_shows_hears_and_confirms_leaves_no_holes(self):
        eng = self.engine()
        product = Product(eng)
        for name, items, corrections in CORPUS:
            product.run(name, items, corrections)
        self.assertEqual([r[1] for r in product.results], ["executed"] * len(CORPUS), product.results)
        self.assertEqual(gate_holes(eng), {"unheard_press": 0, "ungated_mark": 0, "unheard": [], "ungated": []})
        self.assertEqual(eng.unheard_runs(), [])
        self.assertEqual(eng.mismatched_runs(), [])
        self.assertTrue(eng.audit.verify()["ok"])
        withdrawn = [e for e in eng.audit.events() if e["event_type"] == "WITHDRAWN"]
        self.assertEqual(len(withdrawn), 1)                                      # the one scripted correction
        ran = [e["payload"] for e in eng.audit.events() if e["event_type"] == "EXECUTING"]
        self.assertEqual(ran[6]["token"], withdrawn[0]["payload"]["new_token"])  # the corrected form ran as the NEW token

    def test_a_product_that_skips_the_hearing_is_refused(self):
        eng = self.engine()
        product = Product(eng, hears=False)
        for name, items, corrections in CORPUS:
            product.run(name, items, corrections)
        self.assertEqual({r[1:] for r in product.results}, {("refused", "unheard")})
        self.assertEqual([e for e in eng.audit.events() if e["event_type"] == "EXECUTING"], [])
        self.assertEqual(gate_holes(eng)["unheard_press"], 0)                    # nothing ran, so nothing to count

    def test_without_the_flag_the_same_product_runs_unheard_and_the_counter_sees_it(self):
        eng = self.engine(heard=False)                                           # 0.1.2's behaviour: the flag is the rule
        product = Product(eng, hears=False)
        for name, items, corrections in CORPUS:
            product.run(name, items, corrections)
        self.assertEqual([r[1] for r in product.results], ["executed"] * len(CORPUS))
        later = Engine(registry(heard=True), audit=eng.audit, tokens=eng.tokens, record_shown_text=True)
        self.assertEqual(gate_holes(later)["unheard_press"], len(CORPUS))
        self.assertEqual(len(later.unheard_runs()), len(CORPUS))

    def test_an_unnamed_mark_never_runs(self):
        eng = self.engine()
        product = Product(eng, forgets=True)
        for name, items, corrections in CORPUS:
            product.run(name, items, corrections)
        by_name = {r[0]: r[1:] for r in product.results}
        self.assertEqual(by_name["toolbox"], ("not shown", "unshown_mark"))      # Tom was dropped from the words: no showing, no run
        self.assertEqual(by_name["toolbox-everyone"], ("executed", None))        # "everyone" names them all
        self.assertEqual(gate_holes(eng)["ungated_mark"], 0)


if __name__ == "__main__":
    unittest.main()
