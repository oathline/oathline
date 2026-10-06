"""Five-minute tour: a read runs, a write waits for a human, and the audit log proves it.

    python examples/quickstart.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oathline import Capability, Engine, Registry, validate  # noqa: E402

registry = Registry([
    Capability("calendar.read", frozenset({"agent", "alice"}), description="Read today's calendar"),
    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True,
               description="Send an email"),
])
engine = Engine(registry)
engine.register("calendar.read", lambda a: {"events": ["09:00 stand-up", "14:00 review"]})
engine.register("email.send", lambda a: {"sent_to": a["to"], "subject": a["subject"]})

# 1. A model suggests an action. The validator keeps only what is allowed and verbatim.
user_text = "email bob@example.com the notes from stand-up"
model_output = {"intent": "email.send", "approved": True,
                "arguments": {"to": "bob@example.com", "subject": "stand-up"}}
v = validate(model_output, user_text, registry, allowed_arguments=("to", "subject"))
print("validated:", v.candidates, v.arguments, "violations:", v.violations)

# 2. Reads run straight away.
print("read:", engine.request("agent", "calendar.read")["result"])

# 3. Writes are proposed, not executed. The agent cannot confirm them under its own name.
proposal = engine.request("agent", "email.send", v.arguments)
print("proposal:", proposal["state"], proposal["message"])
print("agent tries to confirm:", engine.confirm("agent", proposal["token"]))

# 4. A human proposes and confirms; it runs once.
p2 = engine.request("alice", "email.send", v.arguments)
print("alice confirms:", engine.confirm("alice", p2["token"]))
print("confirm again:", engine.confirm("alice", p2["token"]))

# 5. Every step is in the hash-chained log.
print("audit:", json.dumps(engine.audit.verify()))
for e in engine.audit.events():
    print(f"  #{e['seq']:>2} {e['event_type']:<16} {e['principal']:<6} {e['action']}")
