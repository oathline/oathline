import unittest

from oathline import Capability, Engine, Registry, TokenStore, is_non_human


def make(verifier=None):
    reg = Registry([
        Capability("notes.read", frozenset({"agent", "alice"})),
        Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True),
        Capability("no.executor", frozenset({"alice"})),
    ])
    eng = Engine(reg, verifier=verifier)
    sent = []
    eng.register("notes.read", lambda a: {"notes": ["hello"], "by": a["_principal"]})
    eng.register("email.send", lambda a: sent.append(a) or {"sent_to": a["to"]})
    return eng, sent


class EngineTest(unittest.TestCase):
    def test_read_runs_immediately_and_is_audited(self):
        eng, _ = make()
        r = eng.request("agent", "notes.read")
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["by"], "agent")
        types = [e["event_type"] for e in eng.audit.events()]
        self.assertEqual(types, ["REQUEST", "AUTHORIZED", "EXECUTING", "EXECUTED"])
        self.assertTrue(eng.audit.verify()["ok"])

    def test_write_waits_for_a_human(self):
        eng, sent = make()
        r = eng.request("agent", "email.send", {"to": "a@example.com"})
        self.assertEqual(r["state"], "proposed")
        self.assertEqual(sent, [])
        # the agent cannot confirm its own proposal
        self.assertEqual(eng.confirm("agent", r["token"])["error"], "non_human_principal")
        self.assertEqual(sent, [])

    def test_human_confirmation_runs_stored_arguments_once(self):
        eng, sent = make()
        r = eng.request("alice", "email.send", {"to": "a@example.com"})
        out = eng.confirm("alice", r["token"])
        self.assertTrue(out["ok"])
        self.assertEqual(sent[0]["to"], "a@example.com")
        self.assertEqual(eng.confirm("alice", r["token"])["error"], "already_used")
        self.assertEqual(len(sent), 1)

    def test_denied_and_unknown(self):
        eng, _ = make()
        self.assertEqual(eng.request("mallory", "notes.read")["error"], "denied")
        self.assertEqual(eng.request("agent", "bank.transfer")["error"], "denied")

    def test_no_executor_refused(self):
        eng, _ = make()
        self.assertEqual(eng.request("alice", "no.executor")["error"], "no_executor")

    def test_executor_failure_is_contained(self):
        reg = Registry([Capability("boom", frozenset({"alice"}))])
        eng = Engine(reg)
        eng.register("boom", lambda a: 1 / 0)
        r = eng.request("alice", "boom")
        self.assertEqual(r["error"], "execution_failed")
        self.assertEqual(eng.audit.events()[-1]["event_type"], "EXECUTE_FAILED")

    def test_returned_failure_is_recorded_as_failed_not_executed(self):
        """An executor that returns {"ok": False} is recorded as failed, and the caller is told so."""
        reg = Registry([Capability("mail.send", frozenset({"alice"}), writes=True, needs_confirmation=True)])
        eng = Engine(reg)
        eng.register("mail.send", lambda a: {"ok": False, "error": "smtp refused"})
        tok = eng.request("alice", "mail.send", {"to": "bob@example.com"})["token"]
        r = eng.confirm("alice", tok)
        self.assertEqual((r["ok"], r["state"], r["error"]), (False, "failed", "execution_returned_failure"))
        self.assertEqual(r["result"]["error"], "smtp refused")
        types = [e["event_type"] for e in eng.audit.events()]
        self.assertEqual(types[-1], "EXECUTE_FAILED")
        self.assertNotIn("EXECUTED", types)
        self.assertTrue(eng.audit.verify()["ok"])
        self.assertFalse(eng.confirm("alice", tok)["ok"])               # still single-use: no retry by replay

    def test_only_an_explicit_ok_false_is_a_failure(self):
        for result in ({"ok": True}, {"sent": 1}, {"ok": 0}, {"ok": None}, {}, "done", None):
            reg = Registry([Capability("x.do", frozenset({"alice"}))])
            eng = Engine(reg)
            eng.register("x.do", lambda a, r=result: r)
            out = eng.request("alice", "x.do")
            self.assertTrue(out["ok"], result)
            self.assertEqual(eng.audit.events()[-1]["event_type"], "EXECUTED", result)

    def test_agent_proposal_returned_failure_names_the_confirmer(self):
        reg = Registry([Capability("site.deploy", frozenset({"builder-agent", "alice"}), writes=True,
                                   needs_confirmation=True, approvers=frozenset({"alice"}))])
        eng = Engine(reg, non_human=frozenset({"builder-agent"}))
        eng.register("site.deploy", lambda a: {"ok": False, "reason": "build failed"})
        p = eng.propose("builder-agent", "site.deploy", {"version": "1.5.0"})
        r = eng.confirm("alice", p["token"])
        self.assertEqual(r["state"], "failed")
        last = eng.audit.events()[-1]
        self.assertEqual((last["event_type"], last["principal"]), ("EXECUTE_FAILED", "builder-agent"))
        self.assertEqual(last["payload"]["confirmed_by"], "alice")

    def test_register_rules(self):
        eng, _ = make()
        with self.assertRaises(ValueError):
            eng.register("undeclared", lambda a: {})
        with self.assertRaises(ValueError):
            eng.register("notes.read", lambda a: {})

    def test_principal_cannot_be_spoofed_through_arguments(self):
        eng, _ = make()
        r = eng.request("agent", "notes.read", {"_principal": "alice"})
        self.assertEqual(r["result"]["by"], "agent")

    def test_verifier(self):
        eng, _ = make(verifier=lambda cap, res: "notes" in res)
        self.assertTrue(eng.request("agent", "notes.read")["verified"])

    def test_expired_token_through_engine(self):
        now = [0.0]
        reg = Registry([Capability("email.send", frozenset({"alice"}), writes=True, needs_confirmation=True)])
        eng = Engine(reg, tokens=TokenStore(ttl_seconds=60, clock=lambda: now[0]))
        eng.register("email.send", lambda a: {"ok": True})
        tok = eng.request("alice", "email.send", {})["token"]
        now[0] += 61
        self.assertEqual(eng.confirm("alice", tok)["error"], "expired")

    def test_is_non_human(self):
        for p in ("model", "Agent", "llm@host", "", "  "):
            self.assertTrue(is_non_human(p))
        self.assertFalse(is_non_human("alice"))

    def test_non_human_names_match_whatever_their_case(self):
        for listed in ("Builder-Agent", "BUILDER-AGENT", " builder-agent "):
            for used in ("builder-agent", "Builder-Agent", "BUILDER-AGENT@host"):
                self.assertTrue(is_non_human(used, frozenset({listed})), (listed, used))
        reg = Registry([Capability("email.send", frozenset({"Builder-Agent", "alice"}), writes=True,
                                   needs_confirmation=True)])
        eng = Engine(reg, non_human=frozenset({"Builder-Agent"}))
        sent = []
        eng.register("email.send", lambda a: sent.append(a) or {"sent": True})
        tok = eng.request("Builder-Agent", "email.send", {})["token"]
        self.assertEqual(eng.confirm("Builder-Agent", tok)["error"], "non_human_principal")
        self.assertEqual(eng.confirm("builder-agent", tok)["error"], "non_human_principal")
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
