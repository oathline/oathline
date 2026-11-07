"""Confirmation tokens: a proposed action waits for a human, then runs once.

`propose()` stores the exact (principal, capability, arguments) with an
integrity hash and an expiry and returns a token id. `confirm()` returns the
*stored* arguments - never new ones - and only if the token:

  * exists, belongs to the same principal, has not expired,
  * has not been used before (single use), and
  * still matches its integrity hash (a change to the stored arguments that
    leaves the stored hash alone is refused).

Arguments are stored, hashed and returned in the form JSON reads them back:
number keys become text, tuples become lists. Arguments that JSON cannot
hold (NaN, Infinity, bytes, sets, objects, broken text), or nested more than
32 levels deep, are refused by
`propose()`. The hash is a SHA-256 of that JSON text with sorted keys, so the
same arguments always give the same hash, and `True`, `1`, `1.0` and `"1"`
give four different ones. The hash has no key: someone who can write the
store can change a proposal and its hash together.

A token lives for `ttl_seconds`, the last instant included. Time comes from
the clock the store is given. A clock earlier than the token's creation, or
not a number, is a refusal. A token once refused as `expired` stays expired.
A clock set back to inside the lifetime, before any confirmation has been
refused as `expired`, makes the token live again. The store cannot know.

A stored row that cannot be read as a proposal at all (a wrong type in a
column, an unusable name, an unknown state, arguments that are not a JSON
object) is refused as `corrupt_stored_row`. A readable row whose arguments
no longer match its hash is refused as `tampered`.

Lifetimes are fixed per store and bounded; there is no extend(): a renewal
is a new proposal with a new token.

States: `proposed` (waiting), `used` (confirmed, nothing run yet; the only
state a token can be given back from, by `release()`), `running` (the engine
wrote `EXECUTING` and marked the token with `mark_running()`: no way back,
a confirm is `already_used`), `expired`.

Items (0.2.0, the hearing rule): a proposal may carry `items`, a list of what
will be WRITTEN ({key, value} with optional said, label, kind, shown_as,
whole_word), stored beside the arguments under the same JSON rules with a hash
of their own (`items_hash`, a SHA-256 of the canonical [[key, value], ...]).
The integrity hash of 0.1.2 is unchanged, so a store written by 0.1.2 opens
and its rows read as before, with items None. A row whose items no longer
match their hash is refused as `tampered`. `revise()` makes a NEW proposal
with new items (same principal, capability and arguments); the old one is
left for the engine to withdraw.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .capabilities import name_problem

TTL_MIN, TTL_MAX = 30, 3600
STATES = ("proposed", "used", "running", "expired")   # used: confirmed, nothing run yet; running: the action started
ITEM_SHOWINGS = ("spoken", "screen", "kept", "preset")   # how an item reaches the human (0.2.0); spoken is the default
ITEM_TEXT_FIELDS = ("said", "label", "kind", "shown_as", "whole_word")
_HASH_SHAPE = re.compile(r"[0-9a-f]{64}")
ARGUMENTS_MAX_DEPTH = 32       # objects and lists inside one another; deeper arguments are refused
BUSY_TIMEOUT_SECONDS = 30      # how long one write waits for another writer's transaction before it fails


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Confirmed:
    ok: bool
    capability: str = ""
    arguments: dict | None = None
    error: str = ""
    proposer: str = ""
    confirmer: str = ""
    integrity: str = ""
    items: list | None = None          # 0.2.0: what will be written, as stored; None for a proposal without items
    items_hash: str = ""


def arguments_text(arguments: object) -> str:
    """The canonical JSON text of an arguments object, or ConfigError when it cannot be stored."""
    if type(arguments) is not dict:
        raise ConfigError("arguments must be a dict")
    pending = [(arguments, 1)]                       # depth is checked without recursion, the same on every Python
    while pending:
        value, depth = pending.pop()
        if isinstance(value, (dict, list, tuple)):
            if type(value) not in (dict, list, tuple):
                raise ConfigError("arguments must be plain objects and lists, not subclasses")
            if depth > ARGUMENTS_MAX_DEPTH:
                raise ConfigError("arguments are nested too deeply")
            pending.extend((child, depth + 1) for child in (value.values() if isinstance(value, dict) else value))
    try:
        text = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)
        text.encode("utf-8")
        again = json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), allow_nan=False,
                           ensure_ascii=False)
    except Exception:  # noqa: BLE001 - NaN, bytes, sets, objects, mixed keys, cycles, depth, broken text
        raise ConfigError("arguments cannot be stored as JSON") from None
    return again


def items_text(items: object) -> str:
    """The canonical JSON text of an items list, or ConfigError when it cannot be stored (0.2.0). Each item is a dict
    with a plain-name `key` and a JSON-storable `value`; `said`, `label`, `kind`, `shown_as` and `whole_word` are text
    when given, and `shown_as` is one of ITEM_SHOWINGS. The same JSON and depth rules as the arguments."""
    if type(items) is not list or not items:
        raise ConfigError("items must be a non-empty list")
    for it in items:
        if type(it) is not dict or "key" not in it or "value" not in it:
            raise ConfigError("each item must be a dict with key and value")
        if name_problem(it["key"]):
            raise ConfigError("an item's key must be a plain name")
        for f in ITEM_TEXT_FIELDS:
            if f in it and type(it[f]) is not str:
                raise ConfigError(f"an item's {f} must be text")
        if it.get("shown_as", "spoken") not in ITEM_SHOWINGS:
            raise ConfigError("an item's shown_as must be one of " + ", ".join(ITEM_SHOWINGS))
        if set(it) - {"key", "value", *ITEM_TEXT_FIELDS}:
            raise ConfigError("an item holds a field that is not key, value, " + ", ".join(ITEM_TEXT_FIELDS))
    return arguments_text({"items": items})[9:-1]        # the list's own canonical text, checked by the arguments' rules


def _items_hash(items_s: str) -> str:
    """SHA-256 over the canonical [[key, value], ...] of the stored items text: what the human is shown and hears."""
    pairs = [[it["key"], it["value"]] for it in json.loads(items_s)]
    return hashlib.sha256(json.dumps(pairs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _row_items(items_s):
    """(items, hash, ok): the stored items as a list and their recomputed hash; ok False when the column cannot be read."""
    if items_s is None:
        return None, None, True
    try:
        items = json.loads(items_s)
        if type(items) is not list:
            return None, None, False
        return items, _items_hash(items_s), True
    except Exception:  # noqa: BLE001
        return None, None, False


def _row_arguments(principal, capability, args_text, state, created, expires, integrity) -> dict | None:
    """The stored arguments, when a row can be read as a proposal at all; None when it cannot
    (a wrong type in any column, an unusable name, an unknown state, arguments that are not a JSON object)."""
    if name_problem(principal) or name_problem(capability) or state not in STATES:
        return None
    if type(integrity) is not str or not _HASH_SHAPE.fullmatch(integrity) or type(args_text) is not str:
        return None
    for moment in (created, expires):
        if type(moment) not in (int, float) or not math.isfinite(moment):
            return None
    try:
        arguments = json.loads(args_text)
    except Exception:  # noqa: BLE001
        return None
    return arguments if type(arguments) is dict else None


def _integrity(principal: str, capability: str, arguments: dict) -> str:
    blob = json.dumps([principal, capability, arguments], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TokenStore:
    def __init__(self, path: str = ":memory:", *, ttl_seconds: int = 300,
                 clock: Callable[[], float] = time.time):
        if not isinstance(ttl_seconds, int) or not TTL_MIN <= ttl_seconds <= TTL_MAX:
            raise ConfigError(f"ttl_seconds must be an int in [{TTL_MIN}, {TTL_MAX}]")
        self.ttl = ttl_seconds
        self._clock = clock
        self._db = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            # Opening an existing store writes nothing, so it needs no lock and cannot wait on a writer.
            exists = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposals'").fetchone()
            if not exists:
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, principal TEXT NOT NULL,"
                    " capability TEXT NOT NULL, arguments TEXT NOT NULL, state TEXT NOT NULL,"
                    " created REAL NOT NULL, expires REAL NOT NULL, integrity TEXT NOT NULL, confirmed_by TEXT,"
                    " items TEXT, items_hash TEXT)")
                self._db.commit()
            cols = {r[1] for r in self._db.execute("PRAGMA table_info(proposals)")}
            for col in ("confirmed_by", "items", "items_hash"):   # stores created before a column existed (0.1.x)
                if col not in cols:
                    self._db.execute(f"ALTER TABLE proposals ADD COLUMN {col} TEXT")
                    self._db.commit()
        except BaseException:                            # a failed open leaves no connection behind
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _now(self) -> float | None:
        """The clock as a finite number, or None when the clock cannot be trusted."""
        try:
            now = float(self._clock())
        except Exception:  # noqa: BLE001
            return None
        return now if math.isfinite(now) else None

    def propose(self, principal: str, capability: str, arguments: dict, items: list | None = None) -> str:
        if name_problem(principal) or name_problem(capability):
            raise ConfigError("principal and capability must be plain str names")
        text = arguments_text(arguments)
        arguments = json.loads(text)                 # stored, hashed and later run in this form
        items_s = None if items is None else items_text(items)   # 0.2.0: what will be written, checked the same way
        now = self._now()
        if now is None:
            raise ConfigError("the clock did not give a number")
        return self._insert(principal, capability, text, _integrity(principal, capability, arguments), items_s, now)

    def _insert(self, principal: str, capability: str, args_text: str, integrity: str, items_s, now: float) -> str:
        tid = "tok_" + uuid.uuid4().hex
        self._db.execute("INSERT INTO proposals(id, principal, capability, arguments, state, created, expires,"
                         " integrity, items, items_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (tid, principal, capability, args_text, "proposed", now, now + self.ttl, integrity,
                          items_s, None if items_s is None else _items_hash(items_s)))
        self._db.commit()
        return tid

    def revise(self, token: str, items: list, *, principal: str) -> str | None:
        """0.2.0: a NEW proposal with new items and the same principal, capability and arguments as `token`, which
        must be this principal's and still `proposed`. The old proposal is left as it is: the engine withdraws it
        once the new one is on the record. The new token id, or None when there is nothing to revise."""
        if type(token) is not str or type(principal) is not str:
            return None
        items_s = items_text(items)
        r = self._db.execute("SELECT principal, capability, arguments, state, created, expires, integrity"
                             " FROM proposals WHERE id=?", (token,)).fetchone()
        if not r or _row_arguments(*r) is None or r[0] != principal or r[3] != "proposed":
            return None
        now = self._now()
        if now is None:
            return None
        return self._insert(r[0], r[1], r[2], r[6], items_s, now)

    def get(self, token: str) -> dict | None:
        if not isinstance(token, str):
            return None
        r = self._db.execute("SELECT id, principal, capability, arguments, state, created, expires, integrity,"
                             " items, items_hash FROM proposals WHERE id=?", (token,)).fetchone()
        if not r:
            return None
        keys = ("id", "principal", "capability", "arguments", "state", "created", "expires", "integrity")
        d = dict(zip(keys, r[:8]))
        d["arguments"] = _row_arguments(*r[1:8])     # None when the row cannot be read as a proposal
        items, items_hash, items_ok = _row_items(r[8])
        d["items"], d["items_hash"] = items, (r[9] if items is not None else None)
        d["readable"] = d["arguments"] is not None and items_ok
        return d

    def confirm(self, principal: str, token: str) -> Confirmed:
        """The proposer confirms their own proposal (the original flow)."""
        if type(principal) is not str:
            return Confirmed(False, error="wrong_principal")
        return self._redeem(token, lambda proposer: None if proposer == principal else "wrong_principal")

    def redeem(self, token: str, *, confirmer: str, authorise) -> Confirmed:
        """A DIFFERENT principal confirms (agent proposes -> human confirms).

        `authorise(proposer, capability) -> str | None` is the caller's policy check, run against the STORED
        proposal before the token is consumed; a returned string is the refusal reason. The confirmer is
        recorded on the proposal. Expiry, integrity and single use are enforced here exactly as in confirm()."""
        if type(confirmer) is not str or not confirmer:
            return Confirmed(False, error="no_confirmer")
        return self._redeem(token, None, authorise=authorise, confirmer=confirmer)

    def _redeem(self, token: str, same_principal_check, *, authorise=None, confirmer: str = "") -> Confirmed:
        if type(token) is not str:
            return Confirmed(False, error="unknown_token")
        cur = self._db.execute("SELECT principal, capability, arguments, state, created, expires, integrity,"
                               " items, items_hash FROM proposals WHERE id=?", (token,))
        r = cur.fetchone()
        if not r:
            return Confirmed(False, error="unknown_token")
        p_principal, capability, args_s, state, created, expires, integrity = r[:7]
        items, items_hash_now, items_ok = _row_items(r[7])
        if _row_arguments(*r[:7]) is None or not items_ok:   # not a proposal any engine wrote: trying again will not help
            return Confirmed(False, error="corrupt_stored_row")
        if same_principal_check is not None:
            why = same_principal_check(p_principal)
            if why:
                return Confirmed(False, error=why)
        if state in ("used", "running"):
            return Confirmed(False, error="already_used")
        now = self._now()
        if now is None:
            return Confirmed(False, error="clock_error")
        if state == "expired" or now > expires:
            self._db.execute("UPDATE proposals SET state='expired' WHERE id=? AND state!='used'", (token,))
            self._db.commit()
            return Confirmed(False, error="expired")
        if now < created:                            # a clock earlier than the proposal cannot be trusted
            return Confirmed(False, error="clock_went_back")
        try:
            arguments = json.loads(args_s)
            intact = isinstance(arguments, dict) and integrity == _integrity(p_principal, capability, arguments)
        except Exception:  # noqa: BLE001 - an unreadable row
            intact = False
        if intact and items is not None and r[8] != items_hash_now:   # 0.2.0: the items rewritten, their hash left alone
            intact = False
        if not intact:
            return Confirmed(False, error="tampered")
        if authorise is not None:
            why = authorise(p_principal, capability)
            if why:
                return Confirmed(False, error=why, capability=capability, proposer=p_principal)
        # single use: the state flip is conditional, so two racing confirms cannot both win
        n = self._db.execute("UPDATE proposals SET state='used', confirmed_by=? WHERE id=? AND state='proposed'",
                             (confirmer or p_principal, token)).rowcount
        self._db.commit()
        if n != 1:
            return Confirmed(False, error="already_used")
        return Confirmed(True, capability=capability, arguments=arguments, proposer=p_principal,
                         confirmer=confirmer or p_principal, integrity=integrity, items=items,
                         items_hash=items_hash_now or "")

    def expire_if_due(self, token: str) -> bool:
        """0.2.0: a `proposed` row past its lifetime is marked expired now, so that a showing, a hearing or a
        correction cannot touch a dead proposal. True when the row is expired, then or now."""
        if type(token) is not str:
            return False
        r = self._db.execute("SELECT state, expires FROM proposals WHERE id=?", (token,)).fetchone()
        if not r:
            return False
        state, expires = r
        if state == "expired":
            return True
        now = self._now()
        if state == "proposed" and now is not None and type(expires) in (int, float) and now > expires:
            self._db.execute("UPDATE proposals SET state='expired' WHERE id=? AND state='proposed'", (token,))
            self._db.commit()
            return True
        return False

    def mark_running(self, token: str, confirmer: str) -> bool:
        """The action is about to start: the row goes from `used` to `running`, only for the confirmer that used
        it. From `running` there is no way back: release() refuses it, and a confirm is `already_used`."""
        if type(token) is not str or type(confirmer) is not str:
            return False
        n = self._db.execute("UPDATE proposals SET state='running' WHERE id=? AND state='used' AND confirmed_by=?",
                             (token, confirmer)).rowcount
        self._db.commit()
        return n == 1

    def release(self, token: str, confirmer: str) -> bool:
        """Give a token back after a confirmation that could not be recorded and so ran nothing: the row goes
        from `used` back to `proposed`, only if it was marked used by this same confirmer and the action has
        not started (state `used`, never `running`). True when it did. The store itself refuses anything else,
        so no caller, including code in the same process, can re-arm a token whose action ran (review of 8 Oct)."""
        if type(token) is not str or type(confirmer) is not str:
            return False
        n = self._db.execute("UPDATE proposals SET state='proposed', confirmed_by=NULL"
                             " WHERE id=? AND state='used' AND confirmed_by=?", (token, confirmer)).rowcount
        self._db.commit()
        return n == 1

    def withdraw(self, token: str) -> bool:
        """Expire a proposal that could not be recorded, so it can never run. True when a pending row was expired."""
        if type(token) is not str:
            return False
        n = self._db.execute("UPDATE proposals SET state='expired' WHERE id=? AND state='proposed'", (token,)).rowcount
        self._db.commit()
        return n == 1
