"""An action never runs without a record: one event before the executor is called, one after."""
import hashlib
import json
import unittest

from oathline import AuditLog, Capability, Engine, Registry


class BrokenAudit(AuditLog):
    """An audit log that cannot write the named event types (a full disk, a locked file)."""

    def __init__(self, *broken):
        super().__init__()
        self.broken = set(broken)

    def append(self, event_type, principal, action, payload=None):
        if event_type in self.broken:
            raise OSError("disk full")
        return super().append(event_type, principal, action, payload)


def make(result=None, audit=None, raises=None):
    reg = Registry([Capability("files.read", frozenset({"agent", "alice"})),
                    Capability("files.delete", frozenset({"alice"}), writes=True, needs_confirmation=True)])
    eng = Engine(reg, audit=audit)
    ran = []

    def executor(a):
        ran.append(eng.audit.events()[-1]["event_type"])       # what the log held at the moment it ran
        if raises is not None:
            raise raises
        return result

    eng.register("files.read", executor)
    eng.register("files.delete", executor)
    return eng, ran


def types(eng):
    return [e["event_type"] for e in eng.audit.events()]


class ExecutionRecordTest(unittest.TestCase):
    def test_an_event_is_written_before_the_executor_is_called(self):
        eng, ran = make({"text": "hello"})
        self.assertTrue(eng.request("agent", "files.read")["ok"])
        self.assertEqual(ran, ["EXECUTING"])
        self.assertEqual(types(eng), ["REQUEST", "AUTHORIZED", "EXECUTING", "EXECUTED"])
        self.assertEqual(eng.audit.events()[-1]["payload"]["result"], {"text": "hello"})    # small results in full

    def test_a_20_kb_result_is_recorded_in_bounded_form(self):
        big = {"text": "x" * 20_000}
        eng, ran = make(big)
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["state"]), (True, "executed"))
        self.assertEqual(out["result"], big)                                # the caller still gets all of it
        last = eng.audit.events()[-1]
        self.assertEqual(last["event_type"], "EXECUTED")
        self.assertNotIn("result", last["payload"])
        s = last["payload"]["result_summary"]
        blob = json.dumps(big, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual((s["reason"], s["bytes"], s["sha256"]), ("too_large", len(blob), hashlib.sha256(blob).hexdigest()))
        self.assertEqual(s["preview"], blob[:512].decode("utf-8"))
        self.assertTrue(eng.audit.verify()["ok"])
        self.assertEqual(len(ran), 1)

    def test_a_result_that_is_not_json_storable_is_recorded_in_bounded_form(self):
        for result in ({"blob": b"\x00\x01"}, {"ids": {1, 2}}, object(), {"lone": "\ud800"}, {("a", 1): "tuple key"}):
            eng, ran = make(result)
            out = eng.request("agent", "files.read")
            self.assertEqual((out["ok"], out["state"]), (True, "executed"), result)
            last = eng.audit.events()[-1]
            self.assertEqual(last["event_type"], "EXECUTED", result)
            s = last["payload"]["result_summary"]
            self.assertEqual(s["reason"], "not_json", result)
            self.assertEqual(len(s["sha256"]), 64)
            self.assertLessEqual(len(s["preview"]), 512)
            self.assertTrue(eng.audit.verify()["ok"], result)
            self.assertEqual(len(ran), 1)

    def test_if_the_before_write_fails_the_executor_is_never_called(self):
        eng, ran = make({"text": "hello"}, audit=BrokenAudit("EXECUTING"))
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
        self.assertEqual(ran, [])
        tok = eng.request("alice", "files.delete", {"path": "a"})["token"]
        out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["state"], out["error"]), (False, "not_run", "audit_write_failed"))
        self.assertEqual(ran, [])
        self.assertNotIn("EXECUTED", types(eng))

    def test_if_the_after_write_fails_the_caller_is_told_it_ran_unrecorded(self):
        eng, ran = make({"text": "hello"}, audit=BrokenAudit("EXECUTED"))
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["state"], out["error"]), (False, "ran_unrecorded", "ran_but_not_recorded"))
        self.assertEqual(out["result"], {"text": "hello"})
        self.assertEqual(len(ran), 1)
        self.assertEqual(types(eng)[-1], "EXECUTING")           # the log still shows it started

    def test_an_executor_error_that_cannot_be_recorded_is_also_ran_unrecorded(self):
        eng, ran = make(raises=RuntimeError("boom"), audit=BrokenAudit("EXECUTE_FAILED"))
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["state"], out["error"]), (False, "ran_unrecorded", "ran_but_not_recorded"))
        self.assertEqual(len(ran), 1)

    def test_an_executor_error_is_recorded_after_the_before_event(self):
        eng, ran = make(raises=RuntimeError("boom"))
        self.assertEqual(eng.request("agent", "files.read")["error"], "execution_failed")
        self.assertEqual(types(eng), ["REQUEST", "AUTHORIZED", "EXECUTING", "EXECUTE_FAILED"])

    def test_an_async_executor_is_recorded_as_failed_and_not_awaited(self):
        reg = Registry([Capability("files.read", frozenset({"agent"}))])
        eng = Engine(reg)
        ran = []

        async def executor(a):
            ran.append(1)
            return {"text": "never"}

        eng.register("files.read", executor)
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["error"]), (False, "execution_failed"))
        self.assertEqual(ran, [])
        last = eng.audit.events()[-1]
        self.assertEqual(last["event_type"], "EXECUTE_FAILED")
        self.assertIn("coroutine", last["payload"]["error"])

    def test_system_exit_in_an_executor_is_recorded_then_passed_on(self):
        for error in (SystemExit(3), KeyboardInterrupt()):
            eng, ran = make(raises=error)
            with self.assertRaises(type(error)):
                eng.request("agent", "files.read")
            self.assertEqual(types(eng)[-2:], ["EXECUTING", "EXECUTE_FAILED"])
            self.assertEqual(eng.unfinished(), [])

    def test_a_false_verdict_from_the_verifier_is_reported_and_recorded_and_does_not_undo(self):
        reg = Registry([Capability("files.read", frozenset({"agent"}))])
        eng = Engine(reg, verifier=lambda capability, result: False)
        eng.register("files.read", lambda a: {"text": "x"})
        out = eng.request("agent", "files.read")
        self.assertEqual((out["ok"], out["state"], out["verified"]), (True, "executed", False))
        self.assertEqual(eng.audit.events()[-1]["payload"]["verified"], False)

    def test_a_returned_failure_with_a_large_result_is_bounded_too(self):
        eng, _ = make({"ok": False, "log": "y" * 20_000})
        out = eng.request("agent", "files.read")
        self.assertEqual(out["state"], "failed")
        last = eng.audit.events()[-1]
        self.assertEqual(last["event_type"], "EXECUTE_FAILED")
        self.assertEqual(last["payload"]["result_summary"]["reason"], "too_large")
        self.assertTrue(eng.audit.verify()["ok"])

    def test_number_keys_in_a_result_or_in_arguments_do_not_break_the_chain(self):
        eng, _ = make({1: "a", 10: "b", 2: "c"})
        self.assertTrue(eng.request("agent", "files.read", {1: "x", 10: "y", 2: "z"})["ok"])
        self.assertTrue(eng.audit.verify()["ok"])
        self.assertEqual(eng.audit.events()[-1]["payload"]["result"], {"1": "a", "10": "b", "2": "c"})

    def test_confirmed_write_names_the_confirmer_before_and_after(self):
        reg = Registry([Capability("site.deploy", frozenset({"builder", "alice"}), writes=True, needs_confirmation=True)])
        eng = Engine(reg, non_human=frozenset({"builder"}))
        eng.register("site.deploy", lambda a: {"deployed": True})
        tok = eng.propose("builder", "site.deploy", {})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        before, after = eng.audit.events()[-2:]
        self.assertEqual((before["event_type"], before["principal"], before["payload"]["confirmed_by"]),
                         ("EXECUTING", "builder", "alice"))
        self.assertEqual((before["payload"]["token"], after["payload"]["executing_seq"]), (tok, before["seq"]))
        self.assertEqual((after["event_type"], after["payload"]["confirmed_by"]), ("EXECUTED", "alice"))


if __name__ == "__main__":
    unittest.main()
