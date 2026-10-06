"""Agent proposes -> human confirms -> agent acts.

    python examples/agent_proposes.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oathline import Capability, Engine, Registry  # noqa: E402

registry = Registry([
    Capability("site.deploy", frozenset({"builder-agent", "alice"}), writes=True, needs_confirmation=True,
               approvers=frozenset({"alice"}), description="Deploy a site release"),
])
engine = Engine(registry, non_human=frozenset({"builder-agent", "model"}))
engine.register("site.deploy", lambda a: {"deployed": a["version"], "by": a["_principal"],
                                          "approved_by": a["_confirmed_by"]})

# 1. The agent prepares the work and proposes it. Nothing runs.
p = engine.propose("builder-agent", "site.deploy", {"version": "1.5.0"})
print("proposal:", p["state"], "-", p["message"])

# 2. The agent is on the non-human list, so it cannot approve it under its own name.
print("agent tries to confirm:", engine.confirm("builder-agent", p["token"]))

# 3. A named human approves; the agent's stored action runs exactly once.
print("alice confirms:", engine.confirm("alice", p["token"]))
print("again:", engine.confirm("alice", p["token"]))

# 4. The audit chain shows who proposed, who approved and what ran.
print("audit:", json.dumps(engine.audit.verify()))
for e in engine.audit.events():
    print(f"  #{e['seq']:>2} {e['event_type']:<19} {e['principal']:<14} {e['action']}")
