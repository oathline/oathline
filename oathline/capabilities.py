"""Capability allowlist: deterministic grants, never a model decision.

Every action an agent can take is a named *capability*. A capability declares
which principals may request it, whether it writes (changes the world) and
whether it always needs explicit human confirmation. Anything not declared
is refused.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping

NAME_MAX = 128


class Denied(Exception):
    """Raised when a principal is not granted a capability."""


# Unicode's default-ignorable code points: characters that are letters or marks by category but draw nothing
# (soft hyphen, joiners, fillers, variation selectors, direction and format marks, tags).
_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
              (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
              (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
              (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


# Words in a character's Unicode name that mark it as drawing nothing, or as only changing its neighbours.
_NAME_WORDS = ("BLANK", "FILLER", "JOINER", "SELECTOR", "INVISIBLE", "ZERO WIDTH", "MIRROR")


def not_allowed_in_a_name(ch: str) -> bool:
    """The character rule for names, exactly. A character is refused unless it passes all of these:
      1. ASCII: it is a printable character, space to tilde.
      2. Outside ASCII: its Unicode category is a letter, a mark or a digit (L, M or N). That refuses
         control, format, private, unassigned, space, symbol and punctuation characters.
      3. It is not in Unicode's default-ignorable list (`_IGNORABLE`): soft hyphen, zero-width
         characters, fillers, variation selectors, direction marks, tags.
      4. Python's Unicode table has a name for it. Some real letters have none on some Python versions
         (the Tangut ideographs on Python 3.12, for one) and are refused there.
      5. That name holds none of the words in `_NAME_WORDS` (blank, filler, joiner, selector...).
    The answer depends on the Unicode tables of the Python that runs it. The rule cannot promise how a
    font draws the characters it lets through; names are compared exactly, so a look-alike is another name."""
    cp = ord(ch)
    if cp < 0x80:
        return not 0x20 <= cp < 0x7F
    if unicodedata.category(ch)[0] not in "LMN":
        return True
    if any(low <= cp <= high for low, high in _IGNORABLE):
        return True
    name = unicodedata.name(ch, "")
    return not name or any(word in name for word in _NAME_WORDS)


def name_problem(value: object) -> str | None:
    """Why a principal or capability name is not acceptable, or None when it is a plain name.

    A plain name is a `str` (not a subclass), 1 to 128 characters, with no space at either end, made only
    of the characters `not_allowed_in_a_name` lets through. Names are never changed to make them fit: a
    name with a problem is refused. Two names are the same only when they are the same characters, so
    another case or a look-alike letter is a different name."""
    if type(value) is not str:
        return "not_text"
    if not value:
        return "empty"
    if len(value) > NAME_MAX:
        return "too_long"
    if value != value.strip():
        return "edge_space"
    if any(not_allowed_in_a_name(ch) for ch in value):
        return "hidden_character"
    return None


def _name_set(owner: str, field: str, value: object) -> frozenset:
    if isinstance(value, (str, bytes)) or not isinstance(value, (set, frozenset, list, tuple)):
        raise TypeError(f"{owner}: {field} must be a set of names, not {type(value).__name__}")
    names = tuple(value)                             # read once: what is checked is what is kept
    for n in names:
        if type(n) is not str:
            raise TypeError(f"{owner}: {field} must hold only plain str names, not {type(n).__name__}")
    return frozenset(names)


@dataclass(frozen=True)
class Capability:
    name: str
    principals: frozenset[str]
    writes: bool = False
    needs_confirmation: bool = False
    description: str = ""
    # Who may confirm a proposal made by an AGENT for this capability. Empty = any HUMAN principal granted the
    # capability. Set it to name the only people allowed to approve (e.g. frozenset({"alice"})).
    approvers: frozenset[str] = frozenset()
    # Argument names whose values are secret (a key, a token). The audit log gets a short hash marker for these,
    # never the value. The executor and the confirmation token still get the real value.
    secret_arguments: frozenset[str] = frozenset()
    # Argument names `validate()` may keep for this capability, so a model cannot slip in an undeclared one.
    # `validate(allowed_arguments=...)` adds to these. A mistake here is a TypeError when the capability is built.
    arguments: frozenset[str] = frozenset()

    def __post_init__(self):
        # the name sets are copied into frozensets, so the caller's own set cannot widen a grant later
        for field in ("principals", "approvers", "secret_arguments", "arguments"):
            object.__setattr__(self, field, _name_set(repr(self.name), field, getattr(self, field)))

    @property
    def gated(self) -> bool:
        """True when a run must go through propose -> confirm."""
        return self.writes or self.needs_confirmation


@dataclass(frozen=True)
class Grant:
    capability: str
    writes: bool
    needs_confirmation: bool


class Registry:
    """A frozen set of capabilities. Build it once at start-up; there is no
    runtime API to add or widen a grant."""

    def __init__(self, capabilities: Iterable[Capability]):
        if "_caps" in self.__dict__:
            raise TypeError("a Registry cannot be rebuilt; build a new one")
        caps: dict[str, Capability] = {}
        for c in capabilities:
            if name_problem(c.name) or c.name in caps:
                raise ValueError(f"duplicate or unusable capability name: {c.name!r}")
            for who in (*c.principals, *c.approvers):
                if name_problem(who):
                    raise ValueError(f"{c.name}: unusable principal name {who!r} ({name_problem(who)})")
            if c.writes and not c.needs_confirmation:
                # writes are always confirmed; make that explicit, not implied
                raise ValueError(f"{c.name}: a writing capability must set needs_confirmation=True")
            caps[c.name] = c
        self._caps: Mapping[str, Capability] = MappingProxyType(caps)

    def __setattr__(self, name: str, value: object) -> None:
        if "_caps" in self.__dict__:
            raise TypeError("a Registry is frozen")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise TypeError("a Registry is frozen")

    def __contains__(self, name: object) -> bool:
        return type(name) is str and name in self._caps

    def __iter__(self):
        return iter(self._caps.values())

    def __len__(self) -> int:
        return len(self._caps)

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name) if type(name) is str else None

    def names(self) -> list[str]:
        return sorted(self._caps)

    def authorize(self, principal: str, capability: str) -> Grant:
        if type(principal) is not str or type(capability) is not str:
            raise Denied("principal and capability must be plain str names")
        cap = self._caps.get(capability)
        if cap is None:
            raise Denied(f"unknown capability {capability!r}")
        if principal not in cap.principals:
            raise Denied(f"{principal!r} is not granted {capability!r}")
        return Grant(cap.name, cap.writes, cap.needs_confirmation)
