"""What happens when your code raises something that is not an ordinary Exception
(SystemExit, KeyboardInterrupt, asyncio.CancelledError, GeneratorExit).

The rule, the same in all three places: it is written to the audit log first, then passed on to your program.
  executor        -> EXECUTE_FAILED, then raised
  approver check  -> CONFIRM_REFUSED (approver_check_interrupted), the token is not used, then raised
  verifier        -> VERIFIED false with the error's name (the action has run and is EXECUTED), then raised
An ordinary Exception in the same places is not passed on: it becomes a refusal or a failed result."""
import asyncio
import unittest

from oathline import Capability, Engine, Registry

KINDS = (SystemExit, KeyboardInterrupt, asyncio.CancelledError, GeneratorExit)


def make_hostile(kind, k, shape):
    """An executor whose result (or raised error) raises `kind` on the k-th time any of its special methods,
    properties, its class name or a mapping method is touched. `touches["n"]` counts the touches."""
    touches = {"n": 0}

    def touch(*_a, **_k):
        touches["n"] += 1
        if touches["n"] == k:
            raise kind()

    class Meta(type):
        @property
        def __name__(cls):
            touch()
            return "Hostile"

        @property
        def __mro__(cls):
            touch()
            return type.__dict__["__mro__"].__get__(cls)

    def hostile_body():
        def __getattribute__(self, name):
            touch()
            return object.__getattribute__(self, name)

        def __class__(self):
            touch()
            return type(self)

        def method(self, *a, **kw):
            touch()
            return 0

        def __iter__(self):
            touch()
            return iter(())

        def __repr__(self):
            touch()
            return "hostile"

        return {"__getattribute__": __getattribute__, "__class__": property(__class__), "__repr__": __repr__,
                "__str__": __repr__, "__iter__": __iter__, "__len__": method, "__eq__": method, "__hash__": method,
                "__bool__": method, "keys": method, "get": method, "items": method, "values": method,
                "__getitem__": method, "__contains__": method}

    base = {"result": (object,), "dict_result": (dict,), "list_result": (list,), "error": (Exception,)}[shape]
    Hostile = Meta("Hostile", base, hostile_body())
    if shape.endswith("error"):
        def executor(a):
            raise Hostile()
    else:
        def executor(a):
            return Hostile()
    return executor, touches


def make(**kwargs):
    reg = Registry([Capability("notes.read", frozenset({"agent", "alice"})),
                    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)])
    return Engine(reg, **kwargs)


def types(eng):
    return [e["event_type"] for e in eng.audit.events()]


class InterruptsTest(unittest.TestCase):
    def test_executor(self):
        for kind in KINDS:
            eng = make()
            ran = []

            def executor(a, kind=kind):
                ran.append(1)
                raise kind()

            eng.register("notes.read", executor)
            eng.register("email.send", executor)
            with self.assertRaises(kind):
                eng.request("agent", "notes.read")
            self.assertEqual(types(eng)[-2:], ["EXECUTING", "EXECUTE_FAILED"], kind.__name__)
            self.assertIn(kind.__name__, eng.audit.events()[-1]["payload"]["error"])
            tok = eng.request("agent", "email.send", {})["token"]
            with self.assertRaises(kind):
                eng.confirm("alice", tok)
            self.assertEqual(types(eng)[-2:], ["EXECUTING", "EXECUTE_FAILED"], kind.__name__)
            self.assertEqual(eng.unfinished(), [])
            self.assertEqual(eng.confirm("alice", tok)["error"], "already_used")      # the token is spent
            self.assertEqual(len(ran), 2)
            self.assertTrue(eng.audit.verify()["ok"])

    def test_approver_check(self):
        for kind in KINDS:
            def check(principal, proof, action, kind=kind):
                raise kind()

            eng = make(approver_check=check)
            ran = []
            eng.register("email.send", lambda a: ran.append(1) or {"sent": True})
            tok = eng.request("agent", "email.send", {})["token"]
            with self.assertRaises(kind):
                eng.confirm("alice", tok, proof="p")
            last = eng.audit.events()[-1]
            self.assertEqual((last["event_type"], last["principal"], last["payload"]["reason"]),
                             ("CONFIRM_REFUSED", "alice", "approver_check_interrupted"), kind.__name__)
            self.assertEqual(last["payload"]["interrupted_by"], kind.__name__)
            self.assertEqual(ran, [])
            self.assertEqual(eng.tokens.get(tok)["state"], "proposed")               # the token is not used
            eng.approver_check = lambda principal, proof, action: True
            self.assertTrue(eng.confirm("alice", tok, proof="p")["ok"])
            self.assertEqual(ran, [1])

    def test_verifier(self):
        for kind in KINDS:
            def verifier(capability, result, kind=kind):
                raise kind()

            eng = make(verifier=verifier)
            ran = []
            eng.register("notes.read", lambda a: ran.append(1) or {"notes": 1})
            with self.assertRaises(kind):
                eng.request("agent", "notes.read")
            self.assertEqual(types(eng)[-3:], ["EXECUTING", "EXECUTED", "VERIFIED"], kind.__name__)
            last = eng.audit.events()[-1]["payload"]
            self.assertEqual((last["verified"], last["interrupted_by"]), (False, kind.__name__))
            self.assertEqual(ran, [1])                                               # the action did run
            self.assertEqual(eng.unfinished(), [])
            self.assertTrue(eng.audit.verify()["ok"])

    def test_an_interrupt_raised_while_the_result_or_error_is_being_read_is_recorded_too(self):
        class StopsWhenShown:
            def __repr__(self):
                raise KeyboardInterrupt()

        class StopsWhenWalked(dict):
            def __iter__(self):
                raise KeyboardInterrupt()

            def items(self):
                raise KeyboardInterrupt()

        class ErrorThatStopsWhenShown(Exception):
            def __str__(self):
                raise SystemExit(4)

        def raises_it(a):
            raise ErrorThatStopsWhenShown()

        for executor, kind, event in ((lambda a: StopsWhenShown(), KeyboardInterrupt, "EXECUTED"),
                                      (lambda a: {"value": StopsWhenShown()}, KeyboardInterrupt, "EXECUTED"),
                                      (lambda a: StopsWhenWalked(a=1), KeyboardInterrupt, "EXECUTED"),
                                      (raises_it, SystemExit, "EXECUTE_FAILED")):
            eng = make()
            eng.register("notes.read", executor)
            with self.assertRaises(kind):
                eng.request("agent", "notes.read")
            self.assertEqual(types(eng)[-2:], ["EXECUTING", event])
            self.assertEqual(eng.unfinished(), [])                                    # the outcome is on the record
            last = eng.audit.events()[-1]["payload"]
            if event == "EXECUTED":
                self.assertEqual(last["result_summary"]["interrupted_by"], kind.__name__)
            else:
                self.assertEqual(last["error"], "unreadable")
            self.assertTrue(eng.audit.verify()["ok"])

    def test_an_interrupt_inside_a_log_write_is_passed_on_and_the_log_can_be_used_again(self):
        """Not recorded: nothing can be written while the write itself is interrupted. The half-made
        write is rolled back, so the log is not left stuck."""
        from oathline import AuditLog
        state = {"n": 0, "stop_at": None}

        def clock():
            state["n"] += 1
            if state["n"] == state["stop_at"]:
                raise KeyboardInterrupt()
            return 1_000_000 + state["n"]

        eng = Engine(Registry([Capability("notes.read", frozenset({"agent"}))]), audit=AuditLog(clock_us=clock))
        ran = []
        eng.register("notes.read", lambda a: ran.append(1) or {"notes": 1})
        state["stop_at"] = 2                                         # the second write of the first call
        with self.assertRaises(KeyboardInterrupt):
            eng.request("agent", "notes.read")
        self.assertEqual(ran, [])
        self.assertFalse(eng.audit._db.in_transaction)
        self.assertTrue(eng.request("agent", "notes.read")["ok"])    # the same log, used again
        self.assertEqual(ran, [1])
        state["stop_at"] = state["n"] + 4                            # the EXECUTED write of the next call
        with self.assertRaises(KeyboardInterrupt):
            eng.request("agent", "notes.read")
        self.assertEqual(ran, [1, 1])                                # it ran; the outcome could not be written
        self.assertEqual(types(eng)[-1], "EXECUTING")
        self.assertEqual(len(eng.unfinished()), 1)                   # and that is visible
        self.assertTrue(eng.request("agent", "notes.read")["ok"])
        self.assertTrue(eng.audit.verify()["ok"])

    def test_sweep_a_hostile_result_or_error_that_raises_on_its_k_th_touch(self):
        """Whatever the executor hands back, and whenever it raises while Oathline looks at it, there is
        exactly one outcome event for the EXECUTING event, the chain verifies, and the interrupt reaches the caller."""
        for kind in (KeyboardInterrupt, SystemExit):
            for shape in ("result", "dict_result", "list_result", "error"):
                reached_quiet = False
                for k in range(1, 41):
                    hostile, touches = make_hostile(kind, k, shape)
                    eng = make()
                    eng.register("notes.read", hostile)
                    eng.register("email.send", hostile)
                    raised = None
                    try:
                        out = eng.request("agent", "notes.read")
                    except BaseException as e:  # noqa: BLE001
                        raised = e
                    events = eng.audit.events()
                    executing = [e for e in events if e["event_type"] == "EXECUTING"]
                    self.assertEqual(len(executing), 1, (kind.__name__, shape, k))
                    outcomes = [e for e in events if e["event_type"] in ("EXECUTED", "EXECUTE_FAILED")
                                and e["payload"].get("executing_seq") == executing[0]["seq"]]
                    self.assertEqual(len(outcomes), 1, (kind.__name__, shape, k))
                    self.assertEqual(eng.unfinished(), [], (kind.__name__, shape, k))
                    self.assertTrue(eng.audit.verify()["ok"], (kind.__name__, shape, k))
                    if touches["n"] >= k:                                       # the k-th touch happened: it raised
                        self.assertIs(type(raised), kind, (kind.__name__, shape, k, out if raised is None else None))
                    else:                                                       # fewer touches than k: a quiet run
                        self.assertIsNone(raised, (kind.__name__, shape, k))
                        reached_quiet = True
                        if shape.endswith("error"):
                            self.assertEqual(out["error"], "execution_failed")
                        else:
                            self.assertTrue(out["ok"])
                self.assertTrue(reached_quiet, (kind.__name__, shape))          # 40 is more than Oathline ever touches

    def test_an_ordinary_exception_is_not_passed_on(self):
        def boom(*a):
            raise RuntimeError("boom")

        eng = make(approver_check=boom, verifier=boom)
        eng.register("notes.read", lambda a: {"notes": 1})
        eng.register("email.send", boom)
        out = eng.request("agent", "notes.read")
        self.assertEqual((out["ok"], out["verified"]), (True, False))
        tok = eng.request("agent", "email.send", {})["token"]
        self.assertEqual(eng.confirm("alice", tok, proof="p")["error"], "approver_proof_invalid")
        eng.approver_check = lambda principal, proof, action: True
        self.assertEqual(eng.confirm("alice", tok, proof="p")["error"], "execution_failed")

    def test_if_the_log_cannot_take_the_record_the_interrupt_is_still_passed_on(self):
        from oathline import AuditLog

        class Broken(AuditLog):
            def append(self, event_type, principal, action, payload=None):
                if event_type in ("EXECUTE_FAILED", "CONFIRM_REFUSED", "VERIFIED"):
                    raise OSError("disk full")
                return super().append(event_type, principal, action, payload)

        def stop(*a):
            raise KeyboardInterrupt()

        eng = Engine(Registry([Capability("notes.read", frozenset({"agent"}))]), audit=Broken())
        eng.register("notes.read", stop)
        with self.assertRaises(KeyboardInterrupt):
            eng.request("agent", "notes.read")
        self.assertEqual(types(eng)[-1], "EXECUTING")
        self.assertEqual(len(eng.unfinished()), 1)


if __name__ == "__main__":
    unittest.main()
