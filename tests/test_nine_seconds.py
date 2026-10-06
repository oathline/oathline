"""The facts examples/nine_seconds.py claims to show, each asserted."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_VOLUMES = ["prod-db", "prod-db-backups", "staging-scratch"]


def load():
    spec = importlib.util.spec_from_file_location("nine_seconds", os.path.join(ROOT, "examples", "nine_seconds.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run():
    lines = []
    mod = load()
    return mod, mod.run(out=lines.append), lines


class NineSecondsTest(unittest.TestCase):
    def test_read_runs(self):
        _, f, _ = run()
        self.assertTrue(f["listing"]["ok"])
        self.assertEqual(f["listing"]["state"], "executed")
        self.assertEqual(f["listing"]["result"]["volumes"], ALL_VOLUMES)

    def test_credential_mismatch_is_recorded_as_a_failure(self):
        _, f, _ = run()
        self.assertEqual(f["mismatch"]["error"], "execution_failed")
        first = f["engine"].audit.events()[:4]
        self.assertEqual([e["event_type"] for e in first], ["REQUEST", "AUTHORIZED", "EXECUTING", "EXECUTE_FAILED"])

    def test_each_delete_returns_a_token_and_does_not_run(self):
        _, f, _ = run()
        self.assertEqual(len(f["deletes"]), 2)
        for d in f["deletes"]:
            self.assertEqual(d["state"], "proposed")
            self.assertTrue(d["token"].startswith("tok_"))
            self.assertNotIn("result", d)
        self.assertEqual(f["delete_calls_after_agent"], [])        # the fake store's delete was never called

    def test_store_unchanged_after_the_agent(self):
        _, f, _ = run()
        self.assertEqual(f["volumes_after_agent"], ALL_VOLUMES)

    def test_agent_cannot_confirm_its_own_request(self):
        _, f, _ = run()
        self.assertEqual(len(f["agent_confirms"]), 2)
        for c in f["agent_confirms"]:
            self.assertEqual((c["ok"], c["error"]), (False, "non_human_principal"))
        self.assertEqual(f["delete_calls_after_agent"], [])

    def test_agent_cannot_confirm_under_the_humans_name(self):
        mod, f, _ = run()
        self.assertEqual([c["error"] for c in f["agent_as_human"]],
                         ["approver_proof_required", "approver_proof_invalid"])
        self.assertEqual(f["delete_calls_after_agent"], [])
        self.assertEqual(f["volumes_after_agent"], ALL_VOLUMES)
        refused = [e["payload"]["reason"] for e in f["engine"].audit.events()
                   if e["event_type"] == "CONFIRM_REFUSED" and e["principal"] == mod.HUMAN][:2]
        self.assertEqual(refused, ["approver_proof_required", "approver_proof_invalid"])

    def test_agent_attempts_fit_in_nine_scripted_seconds(self):
        mod, f, _ = run()
        early = [e for e in f["engine"].audit.events() if e["ts"] <= (mod.T0 + 9) * 1_000_000]
        self.assertEqual(len(early), 18)
        self.assertEqual(early[-1]["event_type"], "CONFIRM_REFUSED")

    def test_audit_holds_every_attempt_and_verifies(self):
        mod, f, _ = run()
        ev = f["engine"].audit.events()
        requests = [(e["principal"], e["action"]) for e in ev if e["event_type"] == "REQUEST"]
        self.assertEqual(requests, [(mod.AGENT, "staging.query"), (mod.AGENT, "volume.list"),
                                    (mod.AGENT, "volume.delete"), (mod.AGENT, "volume.delete"),
                                    (mod.AGENT, "volume.delete")])
        wanted = [e["payload"]["arguments"].get("volume") for e in ev
                  if e["event_type"] == "REQUEST" and e["action"] == "volume.delete"]
        self.assertEqual(wanted, ["prod-db", "prod-db-backups", "staging-scratch"])
        self.assertEqual(len([e for e in ev if e["event_type"] == "PROPOSED_BY_AGENT"]), 3)
        refused = [e for e in ev if e["event_type"] == "CONFIRM_REFUSED" and e["principal"] == mod.AGENT]
        self.assertEqual([e["payload"]["reason"] for e in refused], ["non_human_principal"] * 2)
        self.assertEqual(len(ev), 25)
        v = f["engine"].audit.verify(expected_head=f["engine"].audit.head())
        self.assertTrue(v["ok"])
        self.assertEqual(v["events"], 25)

    def test_named_human_confirms_one_action_and_it_runs_exactly_once(self):
        mod, f, _ = run()
        self.assertTrue(f["human_first"]["ok"])
        self.assertEqual(f["human_first"]["result"], {"deleted": "staging-scratch"})
        self.assertEqual(f["human_again"]["error"], "already_used")
        self.assertEqual(f["cloud"].delete_calls, ["staging-scratch"])
        self.assertEqual(sorted(f["cloud"].volumes), ["prod-db", "prod-db-backups"])
        executed = [e for e in f["engine"].audit.events()
                    if e["event_type"] == "EXECUTED" and e["action"] == "volume.delete"]
        self.assertEqual(len(executed), 1)
        self.assertEqual((executed[0]["principal"], executed[0]["payload"]["confirmed_by"]), (mod.AGENT, mod.HUMAN))

    def test_the_humans_proof_does_not_confirm_a_different_action(self):
        _, f, _ = run()
        self.assertEqual((f["replayed"]["ok"], f["replayed"]["error"]), (False, "approver_proof_invalid"))
        self.assertEqual(f["cloud"].delete_calls, ["staging-scratch"])
        self.assertIn("prod-db", f["cloud"].volumes)

    def test_found_token_is_not_in_the_audit_log_but_reaches_the_store(self):
        mod, f, _ = run()
        import json
        self.assertNotIn(mod.FOUND_TOKEN, json.dumps(f["engine"].audit.events()))
        self.assertFalse(f["token_in_clear"])
        self.assertTrue(f["logged_as"].startswith("secret:sha256:"))
        self.assertEqual(f["cloud"].delete_calls, ["staging-scratch"])     # delete() only accepts the real token

    def test_limit_a_raw_call_with_the_token_goes_around_oathline(self):
        _, f, _ = run()
        self.assertEqual(f["raw"]["deleted_directly"], "prod-db")
        self.assertNotIn("prod-db", f["raw"]["volumes"])
        self.assertEqual(f["raw"]["audit_events_added"], 0)

    def test_output_is_the_same_on_every_run(self):
        _, _, a = run()
        _, _, b = run()
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_readme_shows_the_real_output(self):
        _, _, lines = run()
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        heading = "## A replay of the 9-second delete, through a gate (a simulation)\n"
        section = readme.split(heading, 1)[1].split("\n## ", 1)[0]
        self.assertTrue(section.startswith("\n**The limit.** Oathline only governs calls routed through it."))
        self.assertIn("python examples/nine_seconds.py", section)
        self.assertIn("```\n" + "\n".join(lines) + "\n```", section)


if __name__ == "__main__":
    unittest.main()
