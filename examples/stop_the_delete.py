"""Stop the delete. A scripted stand-in agent (no model, no network) asks to drop the production database.
Oathline shows the exact action to a human, refuses without a yes, refuses a yes to an action that changed,
runs the stored action once on a real yes, and prints the log. Run: python examples/stop_the_delete.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from oathline import Capability, Engine, Registry  # noqa: E402

store = {"prod-db": "4.2 TB", "prod-db-backups": "3 snapshots"}      # a fake volume store; nothing real is touched


def drop(args):                                                     # the only code that touches the store: runs once, from the stored arguments
    return {"dropped": args["name"], "was": store.pop(args["name"])}


def card(name):                                                     # the words the human sees; Core checks the item is in them
    return f"The agent wants to run: drop the database {name}. Approve or reject."


# db.drop is a write that needs a human's yes, and is "heard": it runs only after the human was shown exactly what will run.
# Only the "console" may say what was shown; "agent" and "dana" may request; only dana (a human name) may confirm.
engine = Engine(Registry([Capability("db.drop", frozenset({"agent", "dana"}), writes=True, needs_confirmation=True,
                                     heard=True, products=frozenset({"console"}))]), record_shown_text=True)
engine.register("db.drop", drop)

print("1. the agent asks to drop prod-db")
p = engine.request("agent", "db.drop", {"name": "prod-db"}, items=[{"key": "name", "value": "prod-db", "label": "Database"}])
print("   ->", p["state"], "| store:", sorted(store))
print("2. the agent confirms under its own name")
print("   ->", engine.confirm("agent", p["token"])["error"])
print("3. dana says yes before the console showed her anything")
print("   ->", engine.confirm("dana", p["token"])["error"])
print("4. the console shows dana the exact action, and she has it")
print("   -> shown:", engine.show("console", p["token"], card("prod-db"), channel="card")["ok"],
      "| heard:", engine.hear("console", p["token"], channel="card")["ok"])
print("5. before saying yes, dana corrects the target to prod-db-backups: the action changed")
c = engine.correct("console", p["token"], "name", "prod-db-backups")
print("   ->", c["state"], "| a yes on the old, withdrawn card:", engine.confirm("dana", p["token"])["error"])
print("6. a yes to the corrected action before it was shown")
print("   ->", engine.confirm("dana", c["token"])["error"])
print("7. the console shows the corrected action, dana has it, and says yes")
engine.show("console", c["token"], card("prod-db-backups"), channel="card")
engine.hear("console", c["token"], channel="card")
done = engine.confirm("dana", c["token"])
print("   ->", done["state"], done["result"], "| store:", sorted(store))
print("8. the same yes again")
print("   ->", engine.confirm("dana", c["token"])["error"])
print("9. the log")
for e in engine.audit.events():
    print(f"   {e['seq']:>2} {e['event_type']:<20} {e['principal']}")
print("   chain ok:", engine.audit.verify()["ok"])
