"""The governed action loop: REQUEST -> AUTHORIZE -> PROPOSE -> CONFIRM ->
EXECUTE -> VERIFY, with every stage written to the audit log.

  * An action never runs without a record. `EXECUTING` is written before the executor is called; if
    that write fails, the executor is not called. The outcome is written after (`EXECUTED` or
    `EXECUTE_FAILED`) and names the `EXECUTING` event it belongs to. A result over RESULT_MAX_BYTES,
    or one that cannot be stored as JSON, is recorded as its size, a SHA-256 and a preview. If the
    after-write still fails, the caller gets state `ran_unrecorded`. An `EXECUTING` with no outcome
    means: started, outcome not recorded (`unfinished()` lists them).
  * Inputs are checked before anything else. A principal or capability name that is not a plain name
    (see `capabilities.name_problem`: empty, over 128 characters, spaces at the ends, or any character
    outside printable ASCII and Unicode letters, marks and digits) is refused.
    Arguments must be a JSON object of at most ARGUMENTS_MAX_BYTES; they are stored, hashed and run
    in the form JSON reads them back. Refusals are recorded as `REQUEST_REFUSED`.
  * Only capabilities in the registry run, and only for granted principals. Names are compared
    exactly: another case or a look-alike letter is a different name.
  * A capability that writes or needs confirmation is never executed on the
    first call: it returns a token. A second call with that token runs the
    *stored* arguments, after re-authorizing on the stored capability.
  * A principal whose name is on the engine's non-human list (see `is_non_human`) cannot confirm.
    That is a check on a name: a name that is not on the list counts as human. Pass `humans=` to turn
    that round: then only the listed names can confirm and every other name is an agent.
    Without an approver check, any caller of `confirm()` that passes a human's name is treated as
    that human - including an agent that holds the token.
  * Optional approver check: pass `approver_check=fn` and `confirm()` also needs a `proof`.
    `fn(principal, proof, action)` is the integrator's code. `action` says exactly what is being
    confirmed: the token id, the capability, and the stored integrity hash (a SHA-256 over proposer,
    capability and arguments). Anything but `True`, or any error, is a refusal, and the refusal is
    recorded. The proof is never logged. The check guards `confirm()` only: `request()` and
    `propose()` trust the principal name they are given.
  * Executors are registered in code, one per capability. No executor, no run.
  * When an executor, an approver check or a verifier raises something that is not an ordinary
    Exception (SystemExit, KeyboardInterrupt, asyncio.CancelledError, GeneratorExit), it is written to
    the audit log and then passed on to the caller: `EXECUTE_FAILED`; `CONFIRM_REFUSED` with reason
    `approver_check_interrupted` (the token is not used); or `VERIFIED` false. The executor's result
    or error is looked at once, under a guard (`_read_result`, `_error_text`), and nothing of it is
    read outside that guard: whatever it raises, on whichever touch, exactly one outcome event is
    written for the `EXECUTING` event before the interrupt is passed on. The log gets plain data copied
    from that one read, never the object. Raised anywhere else (a clock function, or a signal arriving
    while Oathline's own code runs), an interrupt passes straight through and is not recorded; a log
    write it lands in is rolled back.
  * An optional verifier checks each result independently of the executor. Its verdict is reported
    and recorded; it does not undo the action.
  * Arguments a capability names in `secret_arguments` are written to the audit log as a short hash
    marker, never in clear. For a capability that is not declared, every argument value is masked.
    Only arguments are masked: what an executor returns or raises is logged as it is, and the token
    store holds the real arguments.
  * The integrity hash of a gated action is logged when it is proposed and again when it runs. If
    someone rewrote the stored proposal in between, the two differ (`mismatched_runs()` lists them).
  * AGENT PROPOSES -> HUMAN CONFIRMS -> AGENT ACTS: a non-human principal granted a gated capability
    may create a proposal (`propose`). Its token is redeemed under a human's name - one of the capability's
    named `approvers` if set, else a human granted the capability. The stored arguments then run once, as the
    agent's action on the human's authority; both principals are recorded in the audit log.
"""
from __future__ import annotations

import json
import re
import types
import unicodedata
from typing import Callable, Iterable

from .audit import AuditLog, canonical, sha
from .capabilities import Denied, Registry, name_problem, not_allowed_in_a_name
from .tokens import ConfigError, TokenStore, arguments_text

Executor = Callable[[dict], dict]
Verifier = Callable[[str, dict], bool]
ApproverCheck = Callable[[str, object, dict], bool]

RESULT_MAX_BYTES = 8192        # a larger result is recorded as size + hash + preview
PREVIEW_BYTES = 512
ARGUMENTS_MAX_BYTES = 8192     # larger arguments are refused
TOKEN_SHAPE = re.compile(r"tok_[0-9a-f]{32}")   # what TokenStore issues; anything else is not a token id

DEFAULT_NON_HUMAN = frozenset({"model", "assistant", "llm", "agent", "system", "scheduler",
                               "runner", "browser", "gateway", "verifier", "oathline"})


def result_for_audit(result: object) -> dict:
    """How a result goes into the audit log: in full when it is JSON and small, else a bounded summary."""
    try:
        blob = canonical(result)
        if len(blob) <= RESULT_MAX_BYTES:
            return {"result": json.loads(blob)}      # plain data from the one read, never the object itself
        reason = "too_large"
    except Exception:  # noqa: BLE001 - not JSON (bytes, a set, an object, a broken string)
        try:
            text = repr(result)
        except Exception:  # noqa: BLE001
            text = f"<{type(result).__name__}: no repr>"
        blob, reason = text.encode("utf-8", "backslashreplace"), "not_json"
    return {"result_summary": {"reason": reason, "bytes": len(blob), "sha256": sha(blob),
                               "preview": blob[:PREVIEW_BYTES].decode("utf-8", "replace")}}


def _is_ordinary(raised: object) -> bool:
    """True for an ordinary Exception. Asks the type only, so no code of the object's runs; if even that
    question raises, the answer is no, and the object is passed on like an interrupt."""
    try:
        return issubclass(type(raised), Exception)
    except BaseException:  # noqa: BLE001
        return False


def _type_name(obj: object) -> str:
    """The name of an object's type, or 'unreadable' when reading it raises (a metaclass can make it)."""
    try:
        return str(type(obj).__name__)[:100]
    except BaseException:  # noqa: BLE001
        return "unreadable"


def _error_text(e: object):
    """What the log gets for a raised error, read under guard: (text, interrupt raised while reading or None)."""
    try:
        return f"{type(e).__name__}: {e}"[:300], None
    except BaseException as raised:  # noqa: BLE001 - the error's own text or class name raised
        return "unreadable", (None if _is_ordinary(raised) else raised)


def _read_result(result: dict):
    """The one look at an executor's result: (what the log gets, whether it says ok False, an interrupt
    raised while looking or None). Everything that can run the result's own code is inside the guard."""
    failure, stop = False, None
    try:
        failure = dict.get(result, "ok") is False
        shown = result_for_audit(result)
    except BaseException as raised:  # noqa: BLE001
        shown = {"result_summary": {"reason": "unreadable", "interrupted_by": _type_name(raised)}}
        stop = None if _is_ordinary(raised) else raised
    return shown, failure, stop


def secret_marker(value: object) -> str:
    """What the audit log holds in place of a secret: the same value gives the same marker."""
    return "secret:sha256:" + sha(canonical(value))[:12]


def _fold(name: object) -> str:
    """A name as the non-human list compares it: compatibility form, every character that is not
    allowed in a name removed, spaces trimmed, case folded. Anything that is not a `str` folds to
    the empty string."""
    if type(name) is not str:
        return ""
    text = unicodedata.normalize("NFKC", name[:1024])
    text = "".join(ch for ch in text if not not_allowed_in_a_name(ch))
    return text.strip().casefold()


def _plain_names(what: str, names: object) -> list:
    """A list of names as given, or TypeError: the list itself must be a collection (not one string),
    and every entry must be exactly a `str`. A `str` subclass, such as a str-based Enum member, is
    refused here, because the comparisons below would never match it."""
    if names is None or isinstance(names, (str, bytes)):
        raise TypeError(f"{what} must be a collection of names, not {type(names).__name__}")
    try:
        entries = list(names)
    except TypeError:
        raise TypeError(f"{what} must be a collection of names, not {type(names).__name__}") from None
    for n in entries:
        if type(n) is not str:
            raise TypeError(f"{what} must hold only plain str names, not {type(n).__name__} "
                            "(for an Enum member, pass its .value)")
    return entries


def is_non_human(principal: object, non_human: Iterable[str] = DEFAULT_NON_HUMAN) -> bool:
    """True for a name on the list (whatever its case or width, with spaces or characters that are not
    allowed in a name mixed in, with or without an @host part), and for anything that is empty or is
    not a `str`. A name that is NOT on the list counts as human, including a look-alike spelled with
    letters from another alphabet."""
    p = _fold(principal)
    if not p:
        _plain_names("non_human", non_human)        # a list that cannot be used is an error, whatever the name
        return True
    local = p.split("@", 1)[0]
    names = frozenset(_fold(n) for n in _plain_names("non_human", non_human))
    return p in names or local in names


class _StoreUnavailable(Exception):
    """The token store could not answer; confirm() turns this into a recorded refusal."""


def _safe(name: object) -> str:
    """A name as it may go into the audit log: itself when it is a plain name, else the word 'invalid'."""
    return name if name_problem(name) is None else "invalid"


class Engine:
    def __init__(self, registry: Registry, *, audit: AuditLog | None = None,
                 tokens: TokenStore | None = None, verifier: Verifier | None = None,
                 non_human: Iterable[str] = DEFAULT_NON_HUMAN,
                 approver_check: ApproverCheck | None = None,
                 humans: Iterable[str] | None = None):
        names = _plain_names("non_human", non_human)
        if humans is not None:
            humans = frozenset(_plain_names("humans", humans))
            for h in humans:
                if name_problem(h) or is_non_human(h, names):
                    raise ValueError(f"humans: {h!r} is not a usable name, or is on the non-human list")
        # When `humans` is given, only those exact names can confirm, and every other name is an agent.
        self.humans = humans
        self.registry = registry
        self.audit = audit or AuditLog()
        self.tokens = tokens or TokenStore()
        self.verifier = verifier
        self.non_human = frozenset(names)
        self.approver_check = approver_check
        self._executors: dict[str, Executor] = {}

    def close(self) -> None:
        """Close the audit log and the token store. An engine left open holds its SQLite connections."""
        self.audit.close()
        self.tokens.close()

    def register(self, capability: str, fn: Executor) -> None:
        if name_problem(capability):
            raise ValueError(f"cannot register an executor: {capability!r} is not a plain capability name")
        if capability not in self.registry:
            raise ValueError(f"cannot register an executor for undeclared capability {capability!r}")
        if capability in self._executors:
            raise ValueError(f"executor already registered for {capability!r}")
        self._executors[capability] = fn

    def _is_agent(self, name: object) -> bool:
        """Not a human, as far as this engine can tell: on the non-human list, or (when `humans` is set)
        simply not one of the listed humans."""
        if self.humans is not None:
            return type(name) is not str or name not in self.humans
        return is_non_human(name, self.non_human)

    def _for_audit(self, capability: str, arguments: dict) -> dict:
        """The arguments as the audit log may hold them: secret values replaced by a hash marker.
        An undeclared capability names no secrets, so every value is masked (fail closed). Markers are
        longer than short values; if the masked form is over ARGUMENTS_MAX_BYTES only a count is logged."""
        cap = self.registry.get(capability)
        if cap is None:
            shown = {k: secret_marker(v) for k, v in arguments.items()}
        else:
            shown = {k: secret_marker(v) if k in cap.secret_arguments else v for k, v in arguments.items()}
        if len(canonical(shown)) > ARGUMENTS_MAX_BYTES:
            return {"arguments_summary": {"reason": "too_large_when_masked", "count": len(arguments)}}
        return {"arguments": shown}

    def _refuse(self, principal: object, capability: object, reason: str) -> dict:
        """Refuse a request before it is a request. Nothing of the bad input goes into the log."""
        self.audit.append("REQUEST_REFUSED", _safe(principal), _safe(capability), {"reason": reason})
        return {"ok": False, "error": reason}

    def _admit(self, principal: object, capability: object, arguments: object):
        """Check the inputs of request() and propose(). Returns (arguments, None) or (None, refusal)."""
        if name_problem(principal):
            return None, self._refuse(principal, capability, "bad_principal")
        if name_problem(capability):
            return None, self._refuse(principal, capability, "bad_capability")
        if arguments is None:
            arguments = {}
        if type(arguments) is not dict:
            return None, self._refuse(principal, capability, "bad_arguments")
        try:
            text = arguments_text(arguments)
        except ConfigError:
            return None, self._refuse(principal, capability, "arguments_not_storable")
        if len(text.encode("utf-8")) > ARGUMENTS_MAX_BYTES:
            return None, self._refuse(principal, capability, "arguments_too_large")
        return json.loads(text), None                 # the form JSON reads back: what is stored, hashed and run

    def _propose(self, principal: str, capability: str, arguments: dict, event: str, extra: dict) -> dict:
        try:
            token = self.tokens.propose(principal, capability, arguments)
            arguments_hash = self.tokens.get(token)["integrity"]
        except Exception:  # noqa: BLE001 - no stored proposal, no token
            return self._refuse(principal, capability, "proposal_not_stored")
        self.audit.append(event, principal, capability,
                          {"token": token, "ttl_seconds": self.tokens.ttl, "arguments_hash": arguments_hash, **extra})
        return {"ok": True, "state": "proposed", "token": token}

    # ------------------------------------------------------------------
    def request(self, principal: str, capability: str, arguments: dict | None = None) -> dict:
        """First call. Read-only capabilities run now; gated ones return a token."""
        arguments, refusal = self._admit(principal, capability, arguments)
        if refusal:
            return refusal
        self.audit.append("REQUEST", principal, capability, self._for_audit(capability, arguments))
        try:
            grant = self.registry.authorize(principal, capability)
        except Denied as e:
            self.audit.append("DENIED", principal, capability, {"reason": str(e)})
            return {"ok": False, "error": "denied", "message": str(e)}
        self.audit.append("AUTHORIZED", principal, capability,
                          {"writes": grant.writes, "needs_confirmation": grant.needs_confirmation})
        cap = self.registry.get(capability)
        if cap is not None and cap.gated:
            agent = self._is_agent(principal)
            out = self._propose(principal, capability, arguments, "PROPOSED_BY_AGENT" if agent else "PROPOSED", {})
            if out["ok"]:
                out["message"] = "Confirmation required; nothing was executed."
            return out
        return self._execute(principal, capability, arguments)

    def propose(self, agent: str, capability: str, arguments: dict | None = None) -> dict:
        """An AGENT proposes a gated action for a human to confirm. Nothing runs here."""
        if not self._is_agent(agent):
            return self.request(agent, capability, arguments)          # a human uses the normal flow
        arguments, refusal = self._admit(agent, capability, arguments)
        if refusal:
            return refusal
        self.audit.append("REQUEST", agent, capability,
                          {**self._for_audit(capability, arguments), "mode": "agent_proposal"})
        try:
            grant = self.registry.authorize(agent, capability)
        except Denied as e:
            self.audit.append("DENIED", agent, capability, {"reason": str(e)})
            return {"ok": False, "error": "denied", "message": str(e)}
        cap = self.registry.get(capability)
        if cap is None or not cap.gated:
            self.audit.append("DENIED", agent, capability, {"reason": "not a gated capability"})
            return {"ok": False, "error": "not_gated",
                    "message": "agent proposals are for capabilities that need a human's confirmation"}
        out = self._propose(agent, capability, arguments, "PROPOSED_BY_AGENT",
                            {"writes": grant.writes, "approvers": sorted(cap.approvers) or "any granted human"})
        if out["ok"]:
            out.update(proposer=agent, message="Proposed by an agent; a human must confirm. Nothing was executed.")
        return out

    def _human_may_confirm(self, human: str, proposer: str, capability: str) -> str | None:
        if not self._is_agent(proposer):
            return "wrong_principal"            # a human's own proposal is confirmed by that human only
        cap = self.registry.get(capability)
        if cap is None:
            return "unknown_capability"
        if cap.approvers:
            return None if human in cap.approvers else "not_an_approver"
        return None if human in cap.principals else "not_an_approver"

    def action(self, token: str) -> dict | None:
        """What a token would run, as the approver check sees it: token id, capability, and the stored
        integrity hash (SHA-256 over proposer, capability and arguments). None for an unknown token.
        This reads the token store directly: if the store cannot be read, the store's error is raised."""
        rec = self.tokens.get(token)
        if rec is None:
            return None
        return {"token": rec["id"], "capability": rec["capability"], "arguments_hash": rec["integrity"]}

    def _proof_refusal(self, principal: str, proof: object, action: dict | None) -> str | None:
        """With an approver check set, a name is not enough: the integrator's check must return True
        for this principal, this proof and this exact action."""
        if action is None:
            return "unknown_token"
        if proof is None:
            return "approver_proof_required"
        try:
            ok = self.approver_check(principal, proof, dict(action)) is True
        except BaseException as e:  # noqa: BLE001
            if _is_ordinary(e):                      # fail closed
                ok = False
            else:                                    # an interrupt: recorded, the token left unused, then passed on
                try:
                    self._refuse_confirm(principal, action["token"], "approver_check_interrupted",
                                         interrupted_by=_type_name(e))
                except Exception:  # noqa: BLE001
                    pass
                raise
        return None if ok else "approver_proof_invalid"

    def _refuse_confirm(self, principal: object, token: object, reason: str, action: str = "confirm",
                        **extra) -> dict:
        shown = token if isinstance(token, str) and TOKEN_SHAPE.fullmatch(token) else "invalid"
        self.audit.append("CONFIRM_REFUSED", _safe(principal), action, {"token": shown, "reason": reason, **extra})
        return {"ok": False, "error": reason}

    def confirm(self, principal: str, token: str, proof: object = None) -> dict:
        """Second call. Runs exactly what was proposed, once.

        Own proposal: the proposer confirms it (original flow). Agent proposal: an authorised human confirms it.
        `principal` is a name the caller supplies. It is refused if it is on the non-human list. If the engine
        has an `approver_check`, `proof` must satisfy it for this exact action; if it has none, the name alone
        is trusted."""
        if is_non_human(principal, self.non_human):
            return self._refuse_confirm(principal, token, "non_human_principal")
        if name_problem(principal):
            return self._refuse_confirm(principal, token, "bad_principal")
        if self.humans is not None and principal not in self.humans:
            return self._refuse_confirm(principal, token, "not_a_listed_human")
        if type(token) is not str or not TOKEN_SHAPE.fullmatch(token):
            return self._refuse_confirm(principal, token, "unknown_token")   # not a token id: its value is not logged
        try:
            return self._confirm(principal, token, proof)
        except _StoreUnavailable:
            return self._refuse_confirm(principal, token, "token_store_unavailable")

    def _store(self, call, *args, **kwargs):
        """One call to the token store. A store that cannot answer (locked, closed, unreadable) is a refusal."""
        try:
            return call(*args, **kwargs)
        except Exception:  # noqa: BLE001
            raise _StoreUnavailable() from None

    def _confirm(self, principal: str, token: str, proof: object) -> dict:
        rec = self._store(self.tokens.get, token)
        if rec is not None and not rec["readable"]:
            return self._refuse_confirm(principal, token, "corrupt_stored_row")   # a stored row no engine wrote
        approved = None
        if self.approver_check is not None:
            approved = None if rec is None else {"token": rec["id"], "capability": rec["capability"],
                                                 "arguments_hash": rec["integrity"]}
            why = self._proof_refusal(principal, proof, approved)
            if why:
                return self._refuse_confirm(principal, token, why)
        if rec is not None and rec["principal"] != principal and self._is_agent(rec["principal"]):
            conf = self._store(self.tokens.redeem, token, confirmer=principal,
                               authorise=lambda proposer, cap: self._human_may_confirm(principal, proposer, cap))
            if not conf.ok:
                return self._refuse_confirm(principal, token, conf.error, rec["capability"], proposer=rec["principal"])
            if approved is not None and conf.integrity != approved["arguments_hash"]:
                return self._refuse_confirm(principal, token, "changed_after_approval", conf.capability)
            self.audit.append("CONFIRMED_BY_HUMAN", principal, conf.capability,
                              {"token": token, "proposer": conf.proposer})
            try:                                     # the agent must still hold the grant it proposed under
                self.registry.authorize(conf.proposer, conf.capability)
            except Denied as e:
                self.audit.append("DENIED", conf.proposer, conf.capability, {"reason": str(e), "confirmed_by": principal})
                return {"ok": False, "error": "denied", "message": str(e)}
            return self._execute(conf.proposer, conf.capability, conf.arguments or {}, confirmed_by=principal,
                                 token=token, arguments_hash=conf.integrity)
        conf = self._store(self.tokens.confirm, principal, token)
        if not conf.ok:
            return self._refuse_confirm(principal, token, conf.error)
        if approved is not None and conf.integrity != approved["arguments_hash"]:
            return self._refuse_confirm(principal, token, "changed_after_approval", conf.capability)
        self.audit.append("CONFIRMED", principal, conf.capability, {"token": token})
        try:
            self.registry.authorize(principal, conf.capability)      # re-check on the STORED capability
        except Denied as e:
            self.audit.append("DENIED", principal, conf.capability, {"reason": str(e)})
            return {"ok": False, "error": "denied", "message": str(e)}
        return self._execute(principal, conf.capability, conf.arguments or {}, token=token,
                             arguments_hash=conf.integrity)

    # ------------------------------------------------------------------
    def unfinished(self) -> list[dict]:
        """Every `EXECUTING` event with no outcome recorded for it: the action started, and the log does
        not say how it ended (a crash, or the log failing). Reads the whole log."""
        events = self._events()
        done = {e["payload"].get("executing_seq") for e in events
                if e["event_type"] in ("EXECUTED", "EXECUTE_FAILED") and type(e["payload"].get("executing_seq")) is int}
        return [e for e in events if e["event_type"] == "EXECUTING" and e["seq"] not in done]

    def _events(self) -> list[dict]:
        """The audit events, each with a dict payload. An event someone added by hand with a payload of
        another shape is read as having an empty one, so these helpers do not fail on it."""
        return [e if type(e["payload"]) is dict else {**e, "payload": {}} for e in self.audit.events()]

    def mismatched_runs(self) -> list[dict]:
        """Gated actions that show a rewritten token store: the integrity hash when they ran differs from
        the hash logged when they were proposed, or one token ran more than once. An expiry that was
        extended leaves nothing here. Reads the whole log."""
        events = self._events()
        proposed = {e["payload"].get("token"): e["payload"].get("arguments_hash") for e in events
                    if e["event_type"] in ("PROPOSED", "PROPOSED_BY_AGENT") and type(e["payload"].get("token")) is str}
        out, runs = [], {}
        for e in events:
            token = e["payload"].get("token")
            if e["event_type"] != "EXECUTING" or type(token) is not str or not token:
                continue
            runs[token] = runs.get(token, 0) + 1
            if proposed.get(token) != e["payload"].get("arguments_hash"):
                out.append({"token": token, "reason": "hash_differs", "proposed_hash": proposed.get(token),
                            "ran_hash": e["payload"].get("arguments_hash")})
        out.extend({"token": token, "reason": "ran_more_than_once", "runs": n} for token, n in runs.items() if n > 1)
        return out

    def _record_outcome(self, event_type: str, principal: str, capability: str, payload: dict, by: dict) -> bool:
        """Write what happened after the executor ran. Never raises; False means it could not be written."""
        for attempt in (payload, {"result_summary": {"reason": "unrecordable"}, **by}):
            try:
                self.audit.append(event_type, principal, capability, attempt)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _execute(self, principal: str, capability: str, arguments: dict, *, confirmed_by: str = "",
                 token: str = "", arguments_hash: str = "") -> dict:
        fn = self._executors.get(capability)
        if fn is None:
            self.audit.append("EXECUTE_REFUSED", principal, capability, {"reason": "no_executor"})
            return {"ok": False, "error": "no_executor"}
        by = {"confirmed_by": confirmed_by} if confirmed_by else {}
        what = {"token": token, "arguments_hash": arguments_hash} if token else {}
        unrecorded = {"ok": False, "state": "ran_unrecorded", "error": "ran_but_not_recorded"}
        try:                                         # no record, no run
            seq = self.audit.append("EXECUTING", principal, capability, {**by, **what})["seq"]
        except Exception:  # noqa: BLE001 - fail closed
            return {"ok": False, "state": "not_run", "error": "audit_write_failed"}
        by = {**by, "executing_seq": seq}            # every outcome names the EXECUTING event it belongs to
        try:
            # the principal the caller named is injected here, never taken from arguments
            # reserved keys are injected here only - never taken from the (model- or agent-supplied) arguments
            call = {k: v for k, v in arguments.items() if k not in ("_principal", "_confirmed_by")}
            call["_principal"] = principal
            if confirmed_by:
                call["_confirmed_by"] = confirmed_by
            result = fn(call)
            if type(result) is types.CoroutineType:
                result.close()
                raise TypeError("the executor returned a coroutine; async executors are not supported")
        except BaseException as e:  # noqa: BLE001 - fail closed and record it, whatever was raised.
            # From here to the outcome record, nothing reads the error outside a guard. An ordinary
            # Exception becomes a refusal. Anything else (SystemExit, KeyboardInterrupt,
            # asyncio.CancelledError, GeneratorExit) is recorded and then passed on to the caller.
            text, stop = _error_text(e)
            recorded = self._record_outcome("EXECUTE_FAILED", principal, capability, {"error": text, **by}, by)
            if not _is_ordinary(e):
                raise                                # recorded, then passed on
            if stop is not None:
                raise stop                           # the same, when it was raised while the error's text was read
            return {"ok": False, "error": "execution_failed"} if recorded else unrecorded
        if type(result) is not dict:                 # type() runs no code of the result's
            result = {"value": result}
        shown, failure, stop = _read_result(result)  # the one guarded look at the result
        if failure:                                  # an action that returns a failure is recorded as failed
            recorded = self._record_outcome("EXECUTE_FAILED", principal, capability,
                                            {"returned_failure": True, **shown, **by}, by)
            if stop is not None:
                raise stop                           # recorded, then passed on
            if not recorded:
                return {**unrecorded, "result": result}
            return {"ok": False, "state": "failed", "error": "execution_returned_failure", "result": result}
        recorded = self._record_outcome("EXECUTED", principal, capability, {**shown, **by}, by)
        if stop is not None:
            raise stop                               # the action ran and is recorded; the interrupt is passed on
        if not recorded:
            return {**unrecorded, "result": result}
        verified = None
        if self.verifier is not None:
            try:
                verified = bool(self.verifier(capability, result))
            except BaseException as e:  # noqa: BLE001
                if _is_ordinary(e):
                    verified = False
                else:                                # recorded as a failed verification, then passed on
                    self._record_outcome("VERIFIED", principal, capability,
                                         {"verified": False, "interrupted_by": _type_name(e)}, by)
                    raise
            if not self._record_outcome("VERIFIED", principal, capability, {"verified": verified}, by):
                return {"ok": True, "state": "executed", "result": result, "verified": verified,
                        "warning": "verification_not_recorded"}
        return {"ok": True, "state": "executed", "result": result, "verified": verified}
