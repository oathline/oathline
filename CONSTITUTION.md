# The Oathline constitution

These are the rules Oathline is built around. They are short on purpose. Rules 1 to 7 are enforced by the package and
have tests. Each rule has limits, and they are not all repeated here: the README section "What Oathline does not
protect against" lists them. Rule 8 is a rule for how you deploy it: nothing in this package enforces it.

1. **The model interprets; deterministic code decides.** A language model may suggest an action. Only code that behaves the
   same way every time decides whether the action runs.
2. **Only allowlisted capabilities run.** Every action is declared up front, with the principals allowed to request it.
   Anything undeclared is refused. The registry has no API to add or widen a grant after it is built. Names are compared
   exactly, and a name with spaces at the ends, or with any character outside printable ASCII and Unicode letters, marks
   and digits, is refused. Oathline does not defend
   against its own process: Python code running alongside it can replace the registry.
3. **Model output is validated, never trusted.** Unknown capabilities are dropped. The validator returns only capability
   names, arguments, a confidence and a list of violations, so no field of the model's output can carry authority. Eleven
   top-level field names are also reported: `permission`, `permissions`, `grant`, `grants`, `approve`, `approved`, `approval`, `authority`, `sudo`, `admin`, `override`. An argument is kept only if it is declared and its value appears in the user's own text as
   whole words, in the same characters and case. One word out of a longer phrase passes.
4. **Anything that changes the world waits for a human.** A writing capability returns a confirmation token instead of
   running. A principal on the non-human list (agents, models, infrastructure identities) can't confirm; a name that is not on the list counts as human unless the engine is given a list of humans. An agent may propose such an action; it is confirmed under a human's name: a named approver where one is set, otherwise a human granted the capability. When an approver check is set, the confirmation also needs a proof that the integrator's code accepts for that exact action; without one, the name passed to `confirm()` is trusted. The action then runs once, as the agent's action on that human's authority, and both are recorded.
5. **Confirmations are single-use and expire.** A token is bound to the exact stored arguments by an integrity hash. It
   runs once, runs only what was proposed, and expires. A renewal is a new proposal.
6. **Actions are on the record before and after they run.** Requests, decisions and refusals go into an append-only,
   hash-chained audit log. An event is written before an executor is called; if it can't be written, the executor is not
   called. The outcome is written after, in full or as a size, a hash and a preview. `verify()` detects edits and
   deletions; it detects truncation or a rewritten tail when the head is anchored elsewhere.
7. **Fail closed.** When a check can't be completed, the action does not run. A refusal the engine decides is recorded
   with its reason. If the audit log itself can't be written, nothing runs and the caller gets an error. If it fails after
   an action has run, the caller is told the action ran unrecorded.
8. **No self-modification.** A governed agent should never change its own code, grants or rules. Changes come from
   people, through normal review and release. The package gives an agent no API to do this, and cannot stop code that runs
   in its own process.

What the constitution does not cover: authenticating people, and what your executors do once they are allowed to run.
See the README's "What Oathline does not protect against".
