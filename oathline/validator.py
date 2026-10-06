"""Deterministic validator for model-proposed actions.

A language model may *interpret* a request; it never *decides*. Whatever the
model returns is passed through `validate()`, a pure function that:

  * keeps only capability names that exist in the registry (max `max_candidates`)
  * reports any top-level field that tries to grant authority (permission,
    grant, approve, ...). The result has no field that carries authority, so
    such a field has no effect; nested fields are not inspected
  * keeps an argument only if its key is declared, its value is text or a
    finite number, and that value appears in the user's own text as whole
    words: the same characters in the same case, starting and ending at a
    space or at an edge of the text. Quotes and brackets around it, and
    sentence punctuation after it, are set aside; runs of spaces count as one.
    No Unicode normalisation is applied, so another form or a look-alike
    letter is refused. A piece cut out of one word is refused ("1" out of
    "1,000.50", "example.com" out of "bob@example.com"). Whatever the user
    wrote as a separate word is accepted ("Jane" out of "Jane Citizen", "1"
    out of "1 000,50"), and so is a word without its closing punctuation
    ("U.S" out of "U.S."). Words are split at white space of any kind. A kept
    value is always text: the number 25 comes back as "25"
  * looks at no more than 100 candidates, 100 arguments and 100,000 characters
    of user text; more than that is reported and nothing of that kind is kept
  * clamps confidence to [0, 1]; anything that is not a number counts as 0

A capability can declare the argument names it accepts (`Capability(...,
arguments=frozenset({"to"}))`); those count as allowed for it, on top of
`allowed_arguments`. `allowed_arguments` is your configuration, not model output: if it is not a
list of plain `str` names, `validate()` raises `TypeError` at once.

`validate()` does not raise on malformed model output. A field of the wrong
type is reported as a violation and dropped (a missing field, or `None`, is
simply absent). If anything inside still goes wrong, the
result is empty with the single violation `validator_error`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .capabilities import Registry

MAX_CANDIDATES_SEEN = 100
MAX_ARGUMENTS_SEEN = 100
MAX_TEXT_CHARS = 100_000
OPENERS = "\"'([{<"                # set aside when they stand just before the value
CLOSERS = "\"')]}>.,;:!?"          # set aside when they stand just after it

FORBIDDEN_KEYS = frozenset({"permission", "permissions", "grant", "grants", "approve",
                            "approved", "approval", "authority", "sudo", "admin", "override"})


@dataclass
class Validated:
    candidates: list[str] = field(default_factory=list)
    arguments: dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    violations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.violations


def appears_verbatim(value: str, text: str) -> bool:
    """True when `value` is in `text` character for character as whole words: it starts and ends at a
    space or an edge of the text, apart from OPENERS just before it and CLOSERS just after it."""
    wanted = " ".join(value.split())
    words = " ".join(text.split())
    if not wanted or len(wanted) > len(words):
        return False
    start = words.find(wanted)
    while start != -1:
        left, right = start, start + len(wanted)
        while left > 0 and words[left - 1] in OPENERS:
            left -= 1
        while right < len(words) and words[right] in CLOSERS:
            right += 1
        if (left == 0 or words[left - 1] == " ") and (right == len(words) or words[right] == " "):
            return True
        start = words.find(wanted, start + 1)
    return False


def validate(proposal: Any, user_text: str, registry: Registry, *,
             allowed_arguments: Iterable[str] = (), max_candidates: int = 3) -> Validated:
    if isinstance(allowed_arguments, (str, bytes)):
        raise TypeError("allowed_arguments must be a list of argument names, not one string")
    allowed_arguments = tuple(allowed_arguments)       # your configuration: a mistake here is an error, at once
    for name in allowed_arguments:
        if type(name) is not str:
            raise TypeError(f"allowed_arguments must hold only plain str names, not {type(name).__name__}")
    try:
        return _validate(proposal, user_text, registry, allowed_arguments, max_candidates)
    except Exception:  # noqa: BLE001 - fail closed: nothing is kept
        return Validated(violations=["validator_error"])


def _validate(proposal: Any, user_text: Any, registry: Registry, allowed_arguments: Iterable[str],
              max_candidates: int) -> Validated:
    out = Validated()
    if not isinstance(proposal, dict):
        out.violations.append("not_an_object")
        return out

    for key in proposal:
        if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
            out.violations.append(f"forbidden_field:{key}")

    raw = proposal.get("candidates")
    if raw is None:
        raw = []
    elif not isinstance(raw, (list, tuple)):
        out.violations.append("candidates_not_a_list")
        raw = []
    intent = proposal.get("intent")
    if intent is not None and not isinstance(intent, str):
        out.violations.append("intent_not_text")
        intent = None
    if len(raw) > MAX_CANDIDATES_SEEN:
        out.violations.append("too_many_candidates")
        raw, intent = [], None
    raw = list(raw)
    if intent and intent not in raw:
        raw.insert(0, intent)
    for name in raw:
        if len(out.candidates) >= max_candidates:
            break
        if not isinstance(name, str):
            out.violations.append("candidate_not_text")
            continue
        if name == "unknown":
            continue
        if name not in registry:
            out.violations.append(f"unknown_capability:{name}")
            continue
        if name not in out.candidates:
            out.candidates.append(name)

    conf = proposal.get("confidence", 0)
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or conf != conf:
        if conf is not None:
            out.violations.append("bad_confidence")
    else:
        try:
            out.confidence = max(0.0, min(1.0, float(conf)))
        except OverflowError:                       # an integer too large for a float
            out.violations.append("bad_confidence")

    allowed = set(allowed_arguments)
    for name in out.candidates:                      # names the capabilities themselves declare
        cap = registry.get(name)
        if cap is not None:
            allowed |= cap.arguments
    text = user_text if isinstance(user_text, str) else ""
    args = proposal.get("arguments")
    if args is None:
        args = {}
    elif not isinstance(args, dict):
        out.violations.append("arguments_not_an_object")
        args = {}
    if len(args) > MAX_ARGUMENTS_SEEN:
        out.violations.append("too_many_arguments")
        args = {}
    if args and len(text) > MAX_TEXT_CHARS:
        out.violations.append("text_too_long")
        args = {}
    for k, v in args.items():
        if k not in allowed:
            out.violations.append(f"argument_not_declared:{k}")
            continue
        if isinstance(v, bool) or not isinstance(v, (str, int, float)) or \
                (isinstance(v, float) and not math.isfinite(v)):
            out.violations.append(f"argument_not_text:{k}")
            continue
        s = " ".join(str(v).split())
        if not appears_verbatim(s, text):
            out.violations.append(f"argument_not_verbatim:{k}")
            continue
        out.arguments[k] = s
    return out
