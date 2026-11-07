"""Items in the token store (0.2.0): what will be WRITTEN, stored beside the arguments with a hash of its own.
Items are checked with the arguments' own JSON rules, a rewritten items column is `tampered`, a store written by
0.1.2 opens and gains the two columns, and `revise()` makes a new proposal with the new items, the old untouched."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest

from oathline import ConfigError, TokenStore


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


ITEMS = [{"key": "to", "value": "bob@example.com", "said": "bob at example dot com", "label": "To"},
         {"key": "amount", "value": 2400000, "said": "two point four million", "label": "Amount"}]


class ItemsInTheStoreTest(unittest.TestCase):
    def setUp(self):
        self.s = TokenStore(ttl_seconds=300)

    def test_propose_stores_the_items_and_their_hash(self):
        t = self.s.propose("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)
        rec = self.s.get(t)
        self.assertEqual(rec["items"], ITEMS)
        self.assertEqual(rec["items_hash"], canonical_hash([["to", "bob@example.com", "bob at example dot com", "", "spoken"],
                                                            ["amount", 2400000, "two point four million", "", "spoken"]]))
        self.assertTrue(rec["readable"])

    def test_a_proposal_without_items_has_none(self):
        t = self.s.propose("agent", "pay.send", {"to": "bob@example.com"})
        rec = self.s.get(t)
        self.assertIsNone(rec["items"])
        self.assertIsNone(rec["items_hash"])

    def test_confirm_carries_the_items(self):
        t = self.s.propose("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)
        c = self.s.confirm("agent", t)
        self.assertTrue(c.ok)
        self.assertEqual(c.items, ITEMS)
        self.assertEqual(c.items_hash, self.s.get(t)["items_hash"])

    def test_rewritten_items_are_tampered(self):
        t = self.s.propose("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)
        self.s._db.execute("UPDATE proposals SET items=? WHERE id=?",
                           (json.dumps([{"key": "to", "value": "mallory@example.com"}]), t))
        self.assertEqual(self.s.confirm("agent", t).error, "tampered")

    def test_items_follow_the_arguments_rules(self):
        bad = [
            "to=bob",                                            # not a list
            [["to", "bob"]],                                     # not dicts
            [{"value": "bob"}],                                  # no key
            [{"key": " to", "value": "bob"}],                    # not a plain name
            [{"key": "n", "value": float("nan")}],               # not JSON
            [{"key": "b", "value": b"bytes"}],
            [{"key": "deep", "value": json.loads("[" * 40 + "]" * 40)}],   # nested too deep
            [{"key": "kind", "value": "x", "kind": 7}],          # kind must be text
            [{"key": "s", "value": "x", "shown_as": "sky"}],     # an unknown showing
        ]
        for items in bad:
            with self.subTest(items=str(items)[:40]):
                with self.assertRaises(ConfigError):
                    self.s.propose("agent", "pay.send", {}, items=items)
        t = self.s.propose("agent", "pay.send", {}, items=[{"key": "grid", "value": {1: "a"}}])
        self.assertEqual(self.s.get(t)["items"][0]["value"], {"1": "a"})   # stored as JSON reads it back

    def test_revise_makes_a_new_proposal_and_leaves_the_old_one(self):
        t = self.s.propose("agent", "pay.send", {"to": "bob@example.com"}, items=ITEMS)
        new_items = [dict(ITEMS[0]), {"key": "amount", "value": 2500000, "said": "two point five million", "label": "Amount"}]
        t2 = self.s.revise(t, new_items, principal="agent")
        self.assertTrue(t2 and t2 != t)
        old, new = self.s.get(t), self.s.get(t2)
        self.assertEqual((old["state"], new["state"]), ("proposed", "proposed"))
        self.assertEqual(new["items"], new_items)
        self.assertNotEqual(new["items_hash"], old["items_hash"])
        self.assertEqual((new["principal"], new["capability"], new["arguments"], new["integrity"]),
                         (old["principal"], old["capability"], old["arguments"], old["integrity"]))

    def test_revise_refuses_what_is_not_a_pending_proposal(self):
        t = self.s.propose("agent", "pay.send", {}, items=ITEMS)
        self.assertIsNone(self.s.revise("tok_" + "0" * 32, ITEMS, principal="agent"))
        self.assertIsNone(self.s.revise(t, ITEMS, principal="mallory"))        # another principal's proposal
        self.assertTrue(self.s.confirm("agent", t).ok)
        self.assertIsNone(self.s.revise(t, ITEMS, principal="agent"))          # used


class HowAnItemIsShownIsInTheHashTest(unittest.TestCase):
    """Claude 22:05, hole 2: the same key and value shown another way (on a screen, as a list, as a preset) is another
    proposal: the items hash covers kind and shown_as, and a rewritten showing is tampered."""

    def setUp(self):
        self.s = TokenStore(ttl_seconds=300)

    def hash_of(self, item):
        return self.s.get(self.s.propose("agent", "pay.send", {}, items=[item]))["items_hash"]

    def test_the_hash_covers_kind_and_shown_as(self):
        plain = {"key": "body", "value": "Dear Bob"}
        variants = [dict(plain, shown_as="screen"), dict(plain, shown_as="preset"), dict(plain, shown_as="kept"),
                    dict(plain, kind="list"), dict(plain, kind="list", shown_as="screen")]
        hashes = [self.hash_of(plain)] + [self.hash_of(v) for v in variants]
        self.assertEqual(len(set(hashes)), len(hashes), hashes)
        self.assertEqual(self.hash_of(plain), self.hash_of(dict(plain, shown_as="spoken")))   # spoken is the default
        self.assertEqual(self.hash_of(plain), self.hash_of(dict(plain, label="Body")))       # a label is words, not a showing

    def test_a_rewritten_showing_is_tampered(self):
        t = self.s.propose("agent", "pay.send", {}, items=[{"key": "body", "value": "Dear Bob", "shown_as": "screen"}])
        self.s._db.execute("UPDATE proposals SET items=? WHERE id=?", (json.dumps([{"key": "body", "value": "Dear Bob"}]), t))
        self.assertEqual(self.s.confirm("agent", t).error, "tampered")


class StoreFrom012Test(unittest.TestCase):
    """A tokens.db written by 0.1.2 has no items columns. It opens, gains them, and its rows read with items None."""

    def test_an_old_store_opens_and_gains_the_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "tokens.db")
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE proposals(id TEXT PRIMARY KEY, principal TEXT NOT NULL, capability TEXT NOT NULL,"
                       " arguments TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,"
                       " integrity TEXT NOT NULL, confirmed_by TEXT)")
            db.commit()
            db.close()
            s = TokenStore(path)
            try:
                cols = {r[1] for r in s._db.execute("PRAGMA table_info(proposals)")}
                self.assertTrue({"items", "items_hash"} <= cols)
                t = s.propose("agent", "pay.send", {"to": "x"})
                self.assertIsNone(s.get(t)["items"])
                self.assertTrue(s.confirm("agent", t).ok)
            finally:
                s.close()
            s2 = TokenStore(path)                      # opening again adds nothing twice
            try:
                self.assertEqual(sum(1 for r in s2._db.execute("PRAGMA table_info(proposals)") if r[1] == "items"), 1)
            finally:
                s2.close()


if __name__ == "__main__":
    unittest.main()
