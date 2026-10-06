"""Agent proposes -> human confirms -> agent acts."""
import unittest

from oathline import Capability, Engine, Registry, TokenStore


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(approvers=frozenset(), clock=None):
    reg = Registry([
        Capability("site.deploy", frozenset({"builder", "alice", "bob"}), writes=True, needs_confirmation=True,
                   approvers=approvers),
        Capability("site.read", frozenset({"builder"})),
    ])
    eng = Engine(reg, tokens=TokenStore(ttl_seconds=300, clock=clock or Clock()),
                 non_human=frozenset({"builder", "scout", "model", "agent"}))
    ran = []
    eng.register("site.deploy", lambda a: ran.append(dict(a)) or {"deployed": a["version"]})
    eng.register("site.read", lambda a: {"ok": True})
    return eng, ran


def events(eng):
    return [e["event_type"] for e in eng.audit.events()]


class AgentProposalTest(unittest.TestCase):
    def test_agent_proposes_human_confirms_agent_acts_once(self):
        eng, ran = make()
        p = eng.propose("builder", "site.deploy", {"version": "1.5.0"})
        self.assertEqual(p["state"], "proposed")
        self.assertEqual(ran, [])
        out = eng.confirm("alice", p["token"])
        self.assertTrue(out["ok"])
        self.assertEqual(ran[0]["version"], "1.5.0")
        self.assertEqual(ran[0]["_principal"], "builder")          # the agent acts...
        self.assertEqual(ran[0]["_confirmed_by"], "alice")          # ...on the human's authority
        self.assertEqual(eng.confirm("alice", p["token"])["error"], "already_used")
        self.assertEqual(len(ran), 1)
        ev = events(eng)
        for needed in ("PROPOSED_BY_AGENT", "CONFIRMED_BY_HUMAN", "EXECUTED"):
            self.assertIn(needed, ev)
        executed = [e for e in eng.audit.events() if e["event_type"] == "EXECUTED"][-1]
        self.assertEqual((executed["principal"], executed["payload"]["confirmed_by"]), ("builder", "alice"))
        self.assertTrue(eng.audit.verify()["ok"])

    def test_non_human_can_never_confirm(self):
        eng, ran = make()
        tok = eng.propose("builder", "site.deploy", {"version": "x"})["token"]
        for who in ("builder", "scout", "model", "agent@host", ""):
            self.assertEqual(eng.confirm(who, tok)["error"], "non_human_principal")
        self.assertEqual(ran, [])
        self.assertIn("CONFIRM_REFUSED", events(eng))

    def test_named_approvers_only(self):
        eng, ran = make(approvers=frozenset({"alice"}))
        tok = eng.propose("builder", "site.deploy", {"version": "x"})["token"]
        self.assertEqual(eng.confirm("bob", tok)["error"], "not_an_approver")   # granted, but not named
        self.assertEqual(eng.confirm("mallory", tok)["error"], "not_an_approver")
        self.assertEqual(ran, [])
        self.assertTrue(eng.confirm("alice", tok)["ok"])                          # still usable after refusals

    def test_default_approver_must_be_a_granted_human(self):
        eng, _ = make()
        tok = eng.propose("builder", "site.deploy", {"version": "x"})["token"]
        self.assertEqual(eng.confirm("mallory", tok)["error"], "not_an_approver")

    def test_expired_and_tampered_refused(self):
        clock = Clock()
        eng, ran = make(clock=clock)
        tok = eng.propose("builder", "site.deploy", {"version": "x"})["token"]
        clock.t += 301
        self.assertEqual(eng.confirm("alice", tok)["error"], "expired")
        tok2 = eng.propose("builder", "site.deploy", {"version": "safe"})["token"]
        eng.tokens._db.execute("UPDATE proposals SET arguments=? WHERE id=?", ('{"version": "evil"}', tok2))
        self.assertEqual(eng.confirm("alice", tok2)["error"], "tampered")
        self.assertEqual(ran, [])

    def test_agent_cannot_smuggle_reserved_keys(self):
        eng, ran = make()
        tok = eng.propose("builder", "site.deploy",
                          {"version": "x", "_principal": "alice", "_confirmed_by": "root"})["token"]
        eng.confirm("alice", tok)
        self.assertEqual((ran[0]["_principal"], ran[0]["_confirmed_by"]), ("builder", "alice"))

    def test_proposal_needs_grant_and_a_gated_capability(self):
        eng, _ = make()
        self.assertEqual(eng.propose("scout", "site.deploy", {})["error"], "denied")       # not granted
        self.assertEqual(eng.propose("builder", "site.read", {})["error"], "not_gated")    # reads just run

    def test_revoked_grant_stops_execution_after_confirm(self):
        eng, ran = make()
        tok = eng.propose("builder", "site.deploy", {"version": "x"})["token"]
        # the registry is frozen; simulate a new deployment without the agent's grant
        eng.registry = Registry([Capability("site.deploy", frozenset({"alice"}), writes=True, needs_confirmation=True)])
        out = eng.confirm("alice", tok)
        self.assertEqual(out["error"], "denied")
        self.assertEqual(ran, [])

    def test_human_own_proposal_flow_unchanged(self):
        eng, ran = make()
        tok = eng.request("alice", "site.deploy", {"version": "y"})["token"]
        self.assertEqual(eng.confirm("bob", tok)["error"], "wrong_principal")   # humans confirm their own only
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertNotIn("_confirmed_by", ran[0])

    def test_agent_request_on_gated_capability_is_an_agent_proposal(self):
        eng, ran = make()
        tok = eng.request("builder", "site.deploy", {"version": "z"})["token"]
        self.assertIn("PROPOSED_BY_AGENT", events(eng))
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(ran[0]["_confirmed_by"], "alice")


if __name__ == "__main__":
    unittest.main()
