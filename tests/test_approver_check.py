"""The optional approver check: when it is set, a name alone does not confirm anything,
and a proof is checked against the exact action being confirmed."""
import hashlib
import hmac
import json
import unittest

from oathline import Capability, Engine, Registry

ALICE_KEY = b"alice-key-for-tests"


def sign(action):
    """What alice's approval screen would produce for one action. The agent does not hold ALICE_KEY."""
    msg = f"{action['token']}|{action['capability']}|{action['arguments_hash']}".encode("utf-8")
    return hmac.new(ALICE_KEY, msg, hashlib.sha256).hexdigest()


def check(principal, proof, action):
    return principal == "alice" and isinstance(proof, str) and hmac.compare_digest(proof, sign(action))


def make(approver_check=check):
    reg = Registry([Capability("site.deploy", frozenset({"builder", "alice"}), writes=True, needs_confirmation=True,
                               approvers=frozenset({"alice"}))])
    eng = Engine(reg, non_human=frozenset({"builder"}), approver_check=approver_check)
    ran = []
    eng.register("site.deploy", lambda a: ran.append(dict(a)) or {"deployed": a["version"]})
    return eng, ran


def last(eng):
    e = eng.audit.events()[-1]
    return e["event_type"], e["principal"], e["payload"].get("reason")


class ApproverCheckTest(unittest.TestCase):
    def test_agent_using_a_humans_name_with_no_proof_is_refused(self):
        eng, ran = make()
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]     # the agent holds the token
        out = eng.confirm("alice", tok)
        self.assertEqual((out["ok"], out["error"]), (False, "approver_proof_required"))
        self.assertEqual(ran, [])
        self.assertEqual(last(eng), ("CONFIRM_REFUSED", "alice", "approver_proof_required"))

    def test_wrong_proof_is_refused(self):
        eng, ran = make()
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        out = eng.confirm("alice", tok, proof="a-guess")
        self.assertEqual((out["ok"], out["error"]), (False, "approver_proof_invalid"))
        self.assertEqual(ran, [])
        self.assertEqual(last(eng), ("CONFIRM_REFUSED", "alice", "approver_proof_invalid"))

    def test_right_proof_runs_once(self):
        eng, ran = make()
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        self.assertEqual(eng.confirm("alice", tok)["error"], "approver_proof_required")   # a refusal does not use it up
        proof = sign(eng.action(tok))
        out = eng.confirm("alice", tok, proof=proof)
        self.assertTrue(out["ok"])
        self.assertEqual(eng.confirm("alice", tok, proof=proof)["error"], "already_used")
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0]["_confirmed_by"], "alice")

    def test_a_proof_made_for_one_action_is_refused_for_another(self):
        eng, ran = make()
        small = eng.request("builder", "site.deploy", {"version": "docs-typo"})["token"]
        big = eng.request("builder", "site.deploy", {"version": "drop-everything"})["token"]
        proof_for_small = sign(eng.action(small))
        out = eng.confirm("alice", big, proof=proof_for_small)                    # the agent replays it
        self.assertEqual((out["ok"], out["error"]), (False, "approver_proof_invalid"))
        self.assertEqual(ran, [])
        self.assertTrue(eng.confirm("alice", small, proof=proof_for_small)["ok"])
        self.assertEqual([r["version"] for r in ran], ["docs-typo"])
        self.assertEqual(eng.confirm("alice", big, proof=proof_for_small)["error"], "approver_proof_invalid")
        self.assertEqual(len(ran), 1)

    def test_same_capability_different_arguments_is_a_different_action(self):
        eng, _ = make()
        a = eng.action(eng.request("builder", "site.deploy", {"version": "1"})["token"])
        b = eng.action(eng.request("builder", "site.deploy", {"version": "2"})["token"])
        self.assertEqual(a["capability"], b["capability"])
        self.assertNotEqual(a["arguments_hash"], b["arguments_hash"])
        self.assertNotEqual(a["token"], b["token"])

    def test_the_check_is_given_exactly_what_is_being_confirmed(self):
        seen = []
        eng, _ = make(approver_check=lambda principal, proof, action: seen.append((principal, proof, action)) or True)
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        eng.confirm("alice", tok, proof="p")
        principal, proof, action = seen[0]
        self.assertEqual((principal, proof), ("alice", "p"))
        self.assertEqual(sorted(action), ["arguments_hash", "capability", "token"])
        self.assertEqual((action["token"], action["capability"]), (tok, "site.deploy"))
        blob = json.dumps(["builder", "site.deploy", {"version": "1"}], sort_keys=True, separators=(",", ":"))
        self.assertEqual(action["arguments_hash"], hashlib.sha256(blob.encode("utf-8")).hexdigest())

    def test_stored_arguments_changed_after_the_check_do_not_run(self):
        eng, ran = make()
        tok = eng.request("builder", "site.deploy", {"version": "safe"})["token"]
        proof = sign(eng.action(tok))

        def swap_then_check(principal, proof, action):
            evil = {"version": "evil"}
            blob = json.dumps(["builder", "site.deploy", evil], sort_keys=True, separators=(",", ":"))
            eng.tokens._db.execute("UPDATE proposals SET arguments=?, integrity=? WHERE id=?",
                                   (json.dumps(evil), hashlib.sha256(blob.encode("utf-8")).hexdigest(), tok))
            return check(principal, proof, action)

        eng.approver_check = swap_then_check
        out = eng.confirm("alice", tok, proof=proof)
        self.assertEqual((out["ok"], out["error"]), (False, "changed_after_approval"))
        self.assertEqual(ran, [])

    def test_unknown_token_is_refused_without_calling_the_check(self):
        called = []
        eng, _ = make(approver_check=lambda *a: called.append(a) or True)
        self.assertEqual(eng.confirm("alice", "tok_nope", proof="p")["error"], "unknown_token")
        self.assertEqual(called, [])
        self.assertIsNone(eng.action("tok_nope"))

    def test_a_check_that_raises_is_a_refusal(self):
        def boom(principal, proof, action):
            raise RuntimeError("identity service down")
        eng, ran = make(approver_check=boom)
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        out = eng.confirm("alice", tok, proof="p")
        self.assertEqual((out["ok"], out["error"]), (False, "approver_proof_invalid"))
        self.assertEqual(ran, [])
        self.assertEqual(last(eng), ("CONFIRM_REFUSED", "alice", "approver_proof_invalid"))

    def test_only_exactly_true_passes(self):
        for answer in (1, "yes", None, [True]):
            eng, ran = make(approver_check=lambda principal, proof, action, a=answer: a)
            tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
            self.assertEqual(eng.confirm("alice", tok, proof="x")["error"], "approver_proof_invalid", answer)
            self.assertEqual(ran, [])

    def test_a_humans_own_proposal_needs_the_proof_too(self):
        eng, ran = make()
        tok = eng.request("alice", "site.deploy", {"version": "2"})["token"]
        self.assertEqual(eng.confirm("alice", tok)["error"], "approver_proof_required")
        self.assertTrue(eng.confirm("alice", tok, proof=sign(eng.action(tok)))["ok"])
        self.assertEqual(len(ran), 1)

    def test_the_proof_is_never_written_to_the_audit_log(self):
        eng, _ = make()
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        proof = sign(eng.action(tok))
        eng.confirm("alice", tok, proof="a-guess")
        eng.confirm("alice", tok, proof=proof)
        text = json.dumps(eng.audit.events())
        self.assertNotIn("a-guess", text)
        self.assertNotIn(proof, text)
        self.assertTrue(eng.audit.verify()["ok"])

    def test_without_the_check_a_name_is_enough(self):
        """Unchanged behaviour, and the documented limit: with no approver check, the name is the human."""
        eng, ran = make(approver_check=None)
        tok = eng.request("builder", "site.deploy", {"version": "1"})["token"]
        self.assertTrue(eng.confirm("alice", tok)["ok"])
        self.assertEqual(len(ran), 1)

    def test_request_and_propose_are_not_proof_checked(self):
        """The documented limit: the check guards confirm() only."""
        eng, _ = make()
        self.assertEqual(eng.request("alice", "site.deploy", {"version": "1"})["state"], "proposed")


if __name__ == "__main__":
    unittest.main()
