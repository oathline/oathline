"""0.3.0, `carried: "block"` (Claude 17:40 Wed: fold it into 0.3.0, which is unreleased). A value too long for Core's 8 KB arguments
is carried by its hash: the ARGUMENT is {"sha256", "chars"} and the person is shown the value whole as a block. Until now the product
re-hashed the block itself (the Guard's `show()`, SIYA's card); now Core does, from the shown text, before it records SHOWN.

The block in the shown text, as the Guard already prints it:
    <label>:                      the item's label (else its key), then a colon, on a line of its own
    | first line of the value      every line of the value marked "| "
    | ...
    --- end of <key> ---

  B1  a `block` item (its key declared in `block_keys`; its value = the argument = {"sha256": 64 hex, "chars": int}) is proposed; its
      words are Core's: "<chars> characters, sha256 <hex>"
  B2  show() finds the block, unmarks it, and refuses unless its SHA-256 and its length are the argument's (`carried_hash_mismatch`);
      a missing block, or a line in it without the mark, is `item_not_in_text`; the right block shows
  B3  the proposer chooses nothing: an undeclared key is `showing_not_declared`; a value that is not {sha256, chars} is
      `said_not_rendered`; an item that is not the argument is `carried_not_argument`
  B4  a stored argument rewritten after the proposal is refused at show (`carried_hash_mismatch`)
  B5  a block may be an argument of a must_show capability (the hash dict is the argument, so it can be shown)
  B6  a block is bytes-faithful (FABLE 19:06 Thu 12 Nov, must-fix): the shown text is split on "\n" only and nothing inside the block
      changes, so a value holding "\r\n" (any file written on Windows), or ending in one, shows, is heard and runs; a value holding
      "\r\n" shown with "\n" is refused; a shown text whose line breaks a channel turned into "\r\n" is refused, never shown
  B7  one block per label (FABLE 19:00 Thu 12 Nov): a shown text in which "<label>:" or "--- end of <key> ---" occurs more than once is
      `item_not_in_text` whichever block comes first, so Core never says SHOWN to a text that shows two different values under one label
"""
import copy
import hashlib
import unittest
from unittest import mock

from oathline import Capability, Engine, Registry

TEXT = "SELECT *\nFROM accounts\nWHERE id = 7;\n-- trailing comment line"
HASH = {"sha256": hashlib.sha256(TEXT.encode("utf-8")).hexdigest(), "chars": len(TEXT)}
ARGS = {"sql": HASH, "db": "prod"}


def cap(**kw):
    base = dict(writes=True, needs_confirmation=True, heard=True, products=frozenset({"guard"}), block_keys=frozenset({"sql"}),
                must_show=frozenset({"sql", "db"}))
    base.update(kw)
    return Capability("db.run", frozenset({"agent", "alice"}), **base)


def make(capability=None):
    eng = Engine(Registry([capability or cap()]))
    ran = []
    eng.register("db.run", lambda a: ran.append({k: v for k, v in a.items() if not k.startswith("_")}) or {"ok": True})
    return eng, ran


def items(sql=None, label="sql"):
    it = {"key": "sql", "value": sql if sql is not None else HASH, "carried": "block"}
    if label:
        it["label"] = label
    return [{"key": "db", "value": "prod"}, it]


def block(text=TEXT, key="sql", label="sql"):
    return "\n".join([f"{label}:"] + ["| " + ln for ln in text.split("\n")] + [f"--- end of {key} ---"])


def shown(body=None):
    return "db.run on prod\n" + (body if body is not None else block()) + "\ncode: ABCD"


class B1_Propose(unittest.TestCase):
    def test_b1(self):
        eng, _ = make()
        out = eng.propose("agent", "db.run", ARGS, items=items())
        self.assertTrue(out["ok"], out)
        stored = {it["key"]: it for it in eng.tokens.get(out["token"])["items"]}
        self.assertEqual(stored["sql"]["said"], f"{len(TEXT)} characters, sha256 {HASH['sha256']}")


class B2_Show(unittest.TestCase):
    def setUp(self):
        self.eng, self.ran = make()
        self.tok = self.eng.propose("agent", "db.run", ARGS, items=items())["token"]

    def show(self, text):
        return self.eng.show("guard", self.tok, text, channel="screen")

    def test_b2_right_block_runs(self):
        self.assertTrue(self.show(shown())["ok"])
        self.assertTrue(self.eng.hear("guard", self.tok, channel="screen")["ok"])
        self.assertTrue(self.eng.confirm("alice", self.tok)["ok"])
        self.assertEqual(self.ran, [ARGS])

    def test_b2_one_character(self):
        out = self.show(shown(block(TEXT.replace("7", "8"))))
        self.assertEqual((out["ok"], out["error"], out.get("key")), (False, "carried_hash_mismatch", "sql"))

    def test_b2_missing_or_unmarked(self):
        self.assertEqual(self.show("db.run on prod\ncode: ABCD")["error"], "item_not_in_text")
        unmarked = block().replace("| FROM accounts", "FROM accounts")
        self.assertEqual(self.show(shown(unmarked))["error"], "item_not_in_text")
        no_end = block().replace("--- end of sql ---", "")
        self.assertEqual(self.show(shown(no_end))["error"], "item_not_in_text")

    def test_b2_length_counts(self):
        eng, _ = make()
        wrong = {"sha256": HASH["sha256"], "chars": HASH["chars"] + 1}
        tok = eng.propose("agent", "db.run", {"sql": wrong, "db": "prod"}, items=items(sql=wrong))["token"]
        out = eng.show("guard", tok, shown(), channel="screen")
        self.assertEqual(out["error"], "carried_hash_mismatch")

    def test_b2_label_line(self):
        eng, _ = make()
        tok = eng.propose("agent", "db.run", ARGS, items=items(label="THE SQL"))["token"]
        self.assertEqual(eng.show("guard", tok, shown(), channel="screen")["error"], "item_not_in_text")
        self.assertTrue(eng.show("guard", tok, shown(block(label="THE SQL")), channel="screen")["ok"])


class B3_ProposerChoosesNothing(unittest.TestCase):
    def test_b3_undeclared(self):
        eng, _ = make(cap(block_keys=frozenset()))
        self.assertEqual(eng.propose("agent", "db.run", ARGS, items=items())["error"], "showing_not_declared")

    def test_b3_not_a_hash(self):
        eng, _ = make()
        bad = {"sha256": "xyz", "chars": 3}
        self.assertEqual(eng.propose("agent", "db.run", {"sql": bad, "db": "prod"}, items=items(sql=bad))["error"], "said_not_rendered")
        self.assertEqual(eng.propose("agent", "db.run", {"sql": TEXT, "db": "prod"}, items=items(sql=TEXT))["error"], "said_not_rendered")

    def test_b3_not_the_argument(self):
        eng, _ = make()
        other = {"sha256": "0" * 64, "chars": 5}
        self.assertEqual(eng.propose("agent", "db.run", ARGS, items=items(sql=other))["error"], "carried_not_argument")


class B4_StoredRewrite(unittest.TestCase):
    def test_b4(self):
        eng, ran = make()
        tok = eng.propose("agent", "db.run", ARGS, items=items())["token"]
        real = eng.tokens.get

        def get(t):
            rec = real(t)
            if t == tok and rec:
                rec = copy.deepcopy(rec)
                rec["arguments"]["sql"] = {"sha256": "1" * 64, "chars": len(TEXT)}
            return rec
        with mock.patch.object(eng.tokens, "get", side_effect=get):
            out = eng.show("guard", tok, shown(), channel="screen")
        self.assertEqual(out["error"], "carried_hash_mismatch")
        self.assertEqual(ran, [])


class B5_MustShow(unittest.TestCase):
    def test_b5(self):
        eng, _ = make()
        out = eng.propose("agent", "db.run", ARGS, items=items()[1:])            # db left out: must_show refuses
        self.assertEqual((out["error"], out.get("key")), ("unshown_argument", "db"))


def hash_of(value):
    return {"sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(), "chars": len(value)}


class B6_BytesFaithful(unittest.TestCase):
    def run_value(self, value, shown_value=None):
        h = hash_of(value)
        args = {"sql": h, "db": "prod"}
        eng, ran = make()
        tok = eng.propose("agent", "db.run", args, items=items(sql=h))["token"]
        out = eng.show("guard", tok, shown(block(value if shown_value is None else shown_value)), channel="screen")
        return out, eng, tok, ran, args

    def test_b6_crlf_value_shows_and_runs(self):
        out, eng, tok, ran, args = self.run_value("SELECT *\r\nFROM accounts\r\nWHERE id = 7;")
        self.assertTrue(out["ok"], out)
        self.assertTrue(eng.hear("guard", tok, channel="screen")["ok"])
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(ran, [args])

    def test_b6_value_ending_in_crlf(self):
        out, *_ = self.run_value("line one\r\nline two\r\n")
        self.assertTrue(out["ok"], out)

    def test_b6_lone_cr_kept(self):
        out, *_ = self.run_value("progress 10%\rprogress 100%\nend")
        self.assertTrue(out["ok"], out)

    def test_b6_crlf_value_shown_as_lf_refused(self):
        out, *_ = self.run_value("a\r\nb", shown_value="a\nb")
        self.assertEqual(out["error"], "carried_hash_mismatch")

    def test_b6_channel_turned_text_crlf_refused(self):
        eng, ran = make()
        tok = eng.propose("agent", "db.run", ARGS, items=items())["token"]
        out = eng.show("guard", tok, shown().replace("\n", "\r\n"), channel="screen")
        self.assertFalse(out["ok"])
        self.assertIn(out["error"], ("item_not_in_text", "carried_hash_mismatch"))
        self.assertEqual(ran, [])


class B7_OneBlockPerLabel(unittest.TestCase):
    def setUp(self):
        self.eng, self.ran = make()
        self.tok = self.eng.propose("agent", "db.run", ARGS, items=items())["token"]

    def show(self, text):
        return self.eng.show("guard", self.tok, text, channel="screen")

    def test_b7_real_block_first(self):
        out = self.show(shown(block() + "\n" + block("DROP TABLE accounts;")))
        self.assertEqual((out["ok"], out.get("error")), (False, "item_not_in_text"))

    def test_b7_real_block_second(self):
        out = self.show(shown(block("DROP TABLE accounts;") + "\n" + block()))
        self.assertEqual((out["ok"], out.get("error")), (False, "item_not_in_text"))

    def test_b7_label_line_twice(self):
        out = self.show("sql:\nsee below\n" + shown())
        self.assertEqual((out["ok"], out.get("error")), (False, "item_not_in_text"))

    def test_b7_end_mark_twice(self):
        out = self.show(shown() + "\n--- end of sql ---")
        self.assertEqual((out["ok"], out.get("error")), (False, "item_not_in_text"))

    def test_b7_one_block_still_shows(self):
        self.assertTrue(self.show(shown())["ok"])
        self.assertEqual(self.ran, [])


if __name__ == "__main__":
    unittest.main()
