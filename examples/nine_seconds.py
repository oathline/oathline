"""A replay of the 9-second delete, through a gate. A simulation: no model, no network, no real credential.

Modelled on a publicly reported 2026 incident: a coding agent hit a credential mismatch, found
a token, and deleted a production database and its backups in 9 seconds with no confirmation
step. Here a scripted stand-in agent makes the same moves, but its calls to the (fake) volume
store are routed through Oathline, and the engine has an approver check: a name is not enough
to confirm. The caller must also present a proof, and the check is given the exact action the
proof is for, so a proof for one action does not confirm another. The found token is declared a secret argument,
so the audit log holds a hash marker for it, not the token.

    python examples/nine_seconds.py

The times are a scripted clock (t+0s, t+3s, ...), not measurements, so the output is the same
on every run.

Limit: Oathline only governs calls routed through it. An agent holding a raw shell and a raw
token goes around it. The last step shows that on a second copy of the store.
"""
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oathline import AuditLog, Capability, Engine, Registry, TokenStore  # noqa: E402
from oathline.engine import DEFAULT_NON_HUMAN  # noqa: E402

AGENT = "coding-agent"
HUMAN = "dana"
FOUND_TOKEN = "demo-token-not-a-real-credential"
HUMAN_KEY = b"demo-key-not-a-real-secret"   # stands in for a key only the human's approval screen holds
T0 = 1_780_000_000          # start of the scripted clock, in seconds


class FakeCloud:
    """Stand-in for an infrastructure API. Whoever holds the token can delete; it never asks twice."""

    def __init__(self):
        self.volumes = {"prod-db": "production database", "prod-db-backups": "backups of prod-db",
                        "staging-scratch": "stale staging volume"}
        self.delete_calls = []

    def list(self):
        return sorted(self.volumes)

    def delete(self, name, token):
        if token != FOUND_TOKEN:
            raise PermissionError("bad token")
        self.delete_calls.append(name)
        del self.volumes[name]
        return {"deleted": name}


def staging_query(_args):
    raise PermissionError("credential mismatch: staging credentials rejected")


def approval_proof(action):
    """What the human's approval screen produces for ONE action: an HMAC of its details under HUMAN_KEY."""
    message = f"{action['token']}|{action['capability']}|{action['arguments_hash']}".encode("utf-8")
    return hmac.new(HUMAN_KEY, message, hashlib.sha256).hexdigest()


def approver_check(principal, proof, action):
    """The integrator's code. Here: the proof must be the HMAC for this principal's key and this exact action."""
    return principal == HUMAN and isinstance(proof, str) and hmac.compare_digest(proof, approval_proof(action))


def run(out=print):
    """Play the script. Prints through `out` and returns the facts for tests/test_nine_seconds.py."""
    now = [0]                                                    # seconds since T0; the script sets it
    cloud = FakeCloud()
    registry = Registry([
        Capability("staging.query", frozenset({AGENT}), description="Query the staging database"),
        Capability("volume.list", frozenset({AGENT, HUMAN}), description="List volumes"),
        Capability("volume.delete", frozenset({AGENT, HUMAN}), writes=True, needs_confirmation=True,
                   approvers=frozenset({HUMAN}), secret_arguments=frozenset({"token"}),
                   description="Delete a volume"),
    ])
    engine = Engine(registry,
                    audit=AuditLog(clock_us=lambda: (T0 + now[0]) * 1_000_000),
                    tokens=TokenStore(clock=lambda: T0 + now[0]),
                    non_human=DEFAULT_NON_HUMAN | {AGENT},
                    approver_check=approver_check)
    engine.register("staging.query", staging_query)
    engine.register("volume.list", lambda a: {"volumes": cloud.list()})
    engine.register("volume.delete", lambda a: cloud.delete(a["volume"], a["token"]))

    def at(seconds, text):
        now[0] = seconds
        out(f"t+{seconds:>2}s  {text}")

    out("The agent's nine seconds (scripted clock):")
    at(0, "agent queries staging")
    mismatch = engine.request(AGENT, "staging.query")
    out(f"       -> {mismatch['error']}: credential mismatch")
    at(2, "agent finds a token in an unrelated file (fake, for this demo)")
    at(3, "agent lists volumes (a read)")
    listing = engine.request(AGENT, "volume.list")
    out(f"       -> ran: {listing['result']['volumes']}")
    deletes = []
    for seconds, volume in ((5, "prod-db"), (6, "prod-db-backups")):
        at(seconds, f"agent deletes {volume} with the found token (a write)")
        d = engine.request(AGENT, "volume.delete", {"volume": volume, "token": FOUND_TOKEN})
        out(f"       -> {d['state']}, confirmation token returned, executed: {'result' in d}")
        deletes.append(d)
    at(7, "agent tries to confirm both requests under its own name")
    agent_confirms = [engine.confirm(AGENT, d["token"]) for d in deletes]
    for c in agent_confirms:
        out(f"       -> refused: {c['error']}")
    at(8, f"agent tries to confirm as '{HUMAN}': first with no proof, then with a guessed proof")
    agent_as_human = [engine.confirm(HUMAN, deletes[0]["token"]),
                      engine.confirm(HUMAN, deletes[1]["token"], proof="a-guess")]
    for c in agent_as_human:
        out(f"       -> refused: {c['error']}")
    at(9, "the store is checked")
    volumes_after_agent = cloud.list()
    delete_calls_after_agent = list(cloud.delete_calls)
    out(f"       -> volumes: {volumes_after_agent}; delete calls that reached the store: {len(delete_calls_after_agent)}")

    out("Later, a named human approves one action:")
    at(60, "agent proposes deleting staging-scratch")
    p = engine.propose(AGENT, "volume.delete", {"volume": "staging-scratch", "token": FOUND_TOKEN})
    out(f"       -> {p['state']}")
    at(90, f"{HUMAN} confirms it, with a proof made for that one action")
    proof = approval_proof(engine.action(p["token"]))
    human_first = engine.confirm(HUMAN, p["token"], proof=proof)
    out(f"       -> {human_first['state']}: {human_first['result']}")
    at(91, f"{HUMAN} confirms the same token again")
    human_again = engine.confirm(HUMAN, p["token"], proof=proof)
    out(f"       -> refused: {human_again['error']}")
    at(92, f"agent replays {HUMAN}'s proof on the prod-db request")
    replayed = engine.confirm(HUMAN, deletes[0]["token"], proof=proof)
    out(f"       -> refused: {replayed['error']}")
    out(f"       volumes now: {cloud.list()}; delete calls that reached the store: {len(cloud.delete_calls)}")

    events = engine.audit.events()
    verify = engine.audit.verify(expected_head=engine.audit.head())
    out(f"Audit log: {verify['events']} events, chain verifies: {verify['ok']}")
    for e in events:
        out(f"  #{e['seq']:>2} t+{e['ts'] // 1_000_000 - T0:>2}s {e['event_type']:<18} {e['principal']:<12} {e['action']}")

    token_in_clear = FOUND_TOKEN in json.dumps(events)
    logged_as = [e for e in events if e["action"] == "volume.delete"][0]["payload"]["arguments"]["token"]
    out(f"The found token in the audit log: in clear: {token_in_clear}; logged as: {logged_as}")

    # The limit. A second copy of the store, and the same token used directly, not through Oathline.
    raw_cloud = FakeCloud()
    raw_cloud.delete("prod-db", FOUND_TOKEN)
    added = len(engine.audit.events()) - len(events)
    out("Limit: the same token used directly on a second copy of the store, not through Oathline:")
    out(f"       -> prod-db deleted: {'prod-db' not in raw_cloud.volumes}; audit events added: {added}")

    return {"engine": engine, "cloud": cloud, "mismatch": mismatch, "listing": listing, "deletes": deletes,
            "agent_confirms": agent_confirms, "agent_as_human": agent_as_human, "volumes_after_agent": volumes_after_agent,
            "delete_calls_after_agent": delete_calls_after_agent, "human_first": human_first,
            "human_again": human_again, "replayed": replayed, "token_in_clear": token_in_clear, "logged_as": logged_as,
            "raw": {"deleted_directly": "prod-db", "volumes": raw_cloud.list(), "audit_events_added": added}}


if __name__ == "__main__":
    run()
