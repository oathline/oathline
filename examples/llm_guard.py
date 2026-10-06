"""Guarding a model's tool call with Oathline. The model call is stubbed so this runs offline;
swap `fake_model` for your own client. Whatever the model says, only the validator's output
reaches the engine.

    python examples/llm_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oathline import Capability, Engine, Registry, validate  # noqa: E402

registry = Registry([
    Capability("orders.lookup", frozenset({"agent"}), description="Look up an order by number"),
    Capability("orders.refund", frozenset({"agent", "support-lead"}), writes=True, needs_confirmation=True,
               description="Refund an order"),
])
engine = Engine(registry)
engine.register("orders.lookup", lambda a: {"order": a["order"], "status": "shipped"})
engine.register("orders.refund", lambda a: {"refunded": a["order"]})


def fake_model(prompt: str) -> dict:
    # A compromised or confused model: invents an order, grants itself approval, asks for a transfer.
    return {"intent": "orders.refund", "candidates": ["bank.transfer"], "approve": True,
            "arguments": {"order": "A-1002", "amount": "999"}, "confidence": 0.97}


def handle(user_text: str) -> dict:
    v = validate(fake_model(user_text), user_text, registry, allowed_arguments=("order",))
    print("violations:", v.violations)
    if not v.candidates or "order" not in v.arguments:
        return {"ok": False, "error": "needs_clarification"}
    return engine.request("agent", v.candidates[0], v.arguments)


print(handle("please refund order A-1001"))        # invented order A-1002 is refused -> clarify
print(handle("please refund order A-1002"))        # verbatim -> proposed, waits for a human
