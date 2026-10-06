# Oathline

A small governance core for AI agents that take real actions.

**The model interprets. Deterministic code decides.** Oathline sits between a language model
and the things it can change (email, databases, payments, files) and makes sure:

- **Only allowlisted capabilities run**, and only for principals granted them. There is no
  runtime API to widen a grant. Names are compared exactly. A name is refused if it has spaces
  at the ends or any character outside printable ASCII and Unicode letters, marks and digits.
  Also refused: everything on Unicode's default-ignorable list (zero-width characters, fillers,
  variation selectors) and any character Unicode names as a blank, filler, joiner or selector.
- **Model output is validated, not trusted.** Unknown capabilities are dropped. `validate()`
  returns only capability names, arguments, a confidence and a list of violations, so no field
  of the model's output can carry authority, whatever it is called. Eleven top-level field
  names are also reported as violations: `permission`, `permissions`, `grant`, `grants`, `approve`, `approved`, `approval`, `authority`, `sudo`, `admin`, `override`.
  Any other name (`authorize`, `role`, ...) is not reported. An argument is kept only if it is
  declared and its value appears in the user's own text as whole words: the same characters in
  the same case, starting and ending at white space or an edge of the text (quotes or brackets
  around it and closing punctuation after it are set aside). A piece cut out of one word is
  refused (`1` out of `1,000.50`, `example.com` out of `bob@example.com`). Whatever the user
  wrote as a separate word is accepted (`Jane` out of `Jane Citizen`, `1` out of `1 000,50`).
  `validate()` does not raise on malformed output.
- **Writes wait for a human.** A writing capability returns a confirmation token instead of
  running. The token is bound to the exact stored arguments by an integrity hash, it expires
  and it works once. A principal on the engine's non-human list can't confirm it under its own
  name. A name that is not on that list counts as human, unless you give the engine a `humans=`
  list: then only those names can confirm. With an approver check set, `confirm()` also needs a
  proof that your code verifies. Without one, anyone who can call `confirm()` with a human's
  name is that human.
- **Actions are recorded before and after they run.** Requests, decisions and refusals are
  written to an append-only, hash-chained audit log. `EXECUTING` is written before an executor
  is called; if that write fails, the executor is not called. The outcome is written after:
  the result in full, or its size, hash and a preview when it is over 8,192 bytes or can't be
  stored as JSON. If that last write fails, the caller is told the action ran unrecorded.
  `verify()` detects a changed field, a deleted or swapped event and a broken hash. It detects
  events cut off the end, or a rewritten tail, only if you keep the head hash somewhere else.

What it does not protect against is listed in full [further down](#what-oathline-does-not-protect-against).

Standard library only. Python 3.10+.

The whole `oathline/` package is 1362 lines of Python in 6 files. Count them yourself from the repository root:

```bash
python -c "import glob; print(sum(1 for f in glob.glob('oathline/*.py') for _ in open(f, encoding='utf-8')))"
```

The repository has 239 tests. `python -m unittest discover -s tests` runs them and prints the count.

An earlier version of Oathline Core recorded 233 events in its hash-chained audit log during one internal job for our own company (30 Sep to 2 Oct 2026).
The job's audit logs are private, so this can't be checked from this repository.

## A replay of the 9-second delete, through a gate (a simulation)

**The limit.** Oathline only governs calls routed through it. An agent holding a raw shell and a
raw token goes around it, and nothing in this repository stops that. The last two lines of the
output below show it happening.

In 2026 a coding agent was publicly reported ([news report, 27 April 2026](https://www.gigazine.net/gsc_news/en/20260427-ai-coding-agent-deleted-database/)) to have deleted a production database and its
backups in 9 seconds, using a token it found, with no confirmation step.
`examples/nine_seconds.py` replays those moves as a simulation: a scripted stand-in agent
(no model, no network, a fake token, a fake volume store) whose calls are routed through Oathline.
The engine in the demo has an approver check set, and the found token is declared a secret argument.

```bash
python examples/nine_seconds.py
```

Expected output. The times are a scripted clock, not measurements, so it is the same on every run:

```
The agent's nine seconds (scripted clock):
t+ 0s  agent queries staging
       -> execution_failed: credential mismatch
t+ 2s  agent finds a token in an unrelated file (fake, for this demo)
t+ 3s  agent lists volumes (a read)
       -> ran: ['prod-db', 'prod-db-backups', 'staging-scratch']
t+ 5s  agent deletes prod-db with the found token (a write)
       -> proposed, confirmation token returned, executed: False
t+ 6s  agent deletes prod-db-backups with the found token (a write)
       -> proposed, confirmation token returned, executed: False
t+ 7s  agent tries to confirm both requests under its own name
       -> refused: non_human_principal
       -> refused: non_human_principal
t+ 8s  agent tries to confirm as 'dana': first with no proof, then with a guessed proof
       -> refused: approver_proof_required
       -> refused: approver_proof_invalid
t+ 9s  the store is checked
       -> volumes: ['prod-db', 'prod-db-backups', 'staging-scratch']; delete calls that reached the store: 0
Later, a named human approves one action:
t+60s  agent proposes deleting staging-scratch
       -> proposed
t+90s  dana confirms it, with a proof made for that one action
       -> executed: {'deleted': 'staging-scratch'}
t+91s  dana confirms the same token again
       -> refused: already_used
t+92s  agent replays dana's proof on the prod-db request
       -> refused: approver_proof_invalid
       volumes now: ['prod-db', 'prod-db-backups']; delete calls that reached the store: 1
Audit log: 25 events, chain verifies: True
  # 0 t+ 0s REQUEST            coding-agent staging.query
  # 1 t+ 0s AUTHORIZED         coding-agent staging.query
  # 2 t+ 0s EXECUTING          coding-agent staging.query
  # 3 t+ 0s EXECUTE_FAILED     coding-agent staging.query
  # 4 t+ 3s REQUEST            coding-agent volume.list
  # 5 t+ 3s AUTHORIZED         coding-agent volume.list
  # 6 t+ 3s EXECUTING          coding-agent volume.list
  # 7 t+ 3s EXECUTED           coding-agent volume.list
  # 8 t+ 5s REQUEST            coding-agent volume.delete
  # 9 t+ 5s AUTHORIZED         coding-agent volume.delete
  #10 t+ 5s PROPOSED_BY_AGENT  coding-agent volume.delete
  #11 t+ 6s REQUEST            coding-agent volume.delete
  #12 t+ 6s AUTHORIZED         coding-agent volume.delete
  #13 t+ 6s PROPOSED_BY_AGENT  coding-agent volume.delete
  #14 t+ 7s CONFIRM_REFUSED    coding-agent confirm
  #15 t+ 7s CONFIRM_REFUSED    coding-agent confirm
  #16 t+ 8s CONFIRM_REFUSED    dana         confirm
  #17 t+ 8s CONFIRM_REFUSED    dana         confirm
  #18 t+60s REQUEST            coding-agent volume.delete
  #19 t+60s PROPOSED_BY_AGENT  coding-agent volume.delete
  #20 t+90s CONFIRMED_BY_HUMAN dana         volume.delete
  #21 t+90s EXECUTING          coding-agent volume.delete
  #22 t+90s EXECUTED           coding-agent volume.delete
  #23 t+91s CONFIRM_REFUSED    dana         volume.delete
  #24 t+92s CONFIRM_REFUSED    dana         confirm
The found token in the audit log: in clear: False; logged as: secret:sha256:17c45716a841
Limit: the same token used directly on a second copy of the store, not through Oathline:
       -> prod-db deleted: True; audit events added: 0
```

## Install

Two ways. Installing from PyPI is enough to use the library; cloning gives you the examples and the
tests as well.

**Python version.** Check it first; you need 3.10 or newer (Oathline prints no warning if it's older):

```bash
python --version
```

If your system has no `python` command, use `python3` (macOS, most Linux) or `py` (Windows).

**A virtual environment (recommended):**

```bash
python -m venv .venv
```

Then activate it with the one line for your shell:

| Shell | Line |
|---|---|
| Windows, Command Prompt or PowerShell | `.venv\Scripts\activate` |
| Windows, Git Bash | `source .venv/Scripts/activate` |
| macOS or Linux | `source .venv/bin/activate` |

**Install from PyPI:**

```bash
python -m pip install oathline
```

**Or clone and install:**

```bash
git clone https://github.com/oathline/oathline.git
cd oathline
python -m pip install .
```

Installing from the clone needs internet too: pip downloads `setuptools>=77` to build the package,
even though Oathline itself has no dependencies.

**Where to run things.** After either install, `import oathline` works from any folder. Without an
install, the snippets in this README only work from the repository root (the `oathline` folder). The
files in `examples/` find the package themselves and run from anywhere.

**Where it has been checked.** By hand: Windows 11 with Python 3.11, 3.12 and 3.14. The CI workflow
(`.github/workflows/tests.yml`) is set to run the tests on Ubuntu, Windows and macOS with Python 3.10, 3.11, 3.12 and 3.13.

## Quickstart (5 lines)

Paste this into a Python file or prompt, run from the repository root (or anywhere after an
install):

```python
from oathline import Capability, Engine, Registry
engine = Engine(Registry([Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True)]))
engine.register("email.send", lambda a: {"sent_to": a["to"]})
token = engine.request("agent", "email.send", {"to": "bob@example.com"})["token"]   # nothing sent yet
print(engine.confirm("alice", token), engine.audit.verify()["ok"])                  # a human confirms; runs once
```

**What success looks like:**

```
{'ok': True, 'state': 'executed', 'result': {'sent_to': 'bob@example.com'}, 'verified': None} True
```

`'state': 'executed'` means the write ran once, after the human confirmed. `'verified': None` means
no verifier was passed to the `Engine`, so no independent check of the result ran (it is not a failure).
The final `True` is the audit chain verifying.

## Examples

| File | Shows |
|---|---|
| `examples/quickstart.py` | a read runs, a write waits for a human, the audit log proves it |
| `examples/llm_guard.py` | validating a (stubbed) model's tool call: invented arguments and self-approval are dropped |
| `examples/agent_proposes.py` | an agent proposes, a named human confirms, the agent acts once |
| `examples/nine_seconds.py` | a scripted agent with a found token tries to delete production and its backups; the deletes wait for a human |

## Five-minute tour

From the repository root:

```bash
python examples/quickstart.py
python -m unittest discover -s tests
```

The example shows a read running, a write waiting for a human, and the audit log proving it.
The 239 tests need no network.

The same flow in your own code. This block runs as written; the stand-ins at the top
(`user_text`, `model_output`, `send_email`, `show_to_a_human`) are where your app plugs in:

```python
from oathline import Capability, Engine, Registry, validate

# Stand-ins for your app: the user's words, your model's reply, your mailer, your approval UI.
user_text = "email bob@example.com the notes from stand-up"
model_output = {"intent": "email.send", "arguments": {"to": "bob@example.com", "subject": "stand-up"}}
def send_email(to, subject):
    return {"sent_to": to, "subject": subject}
def show_to_a_human(token):
    print("waiting for a human to confirm", token)

registry = Registry([
    Capability("calendar.read", frozenset({"agent", "alice"})),
    Capability("email.send", frozenset({"agent", "alice"}), writes=True, needs_confirmation=True),
])
engine = Engine(registry)   # in-memory; "Files and lifetimes" under Reference shows the SQLite form
engine.register("calendar.read", lambda a: {"events": ["09:00 stand-up"]})
engine.register("email.send", lambda a: send_email(a["to"], a["subject"]))  # executors get one dict

# 1. validate what the model proposed
v = validate(model_output, user_text, registry, allowed_arguments=("to", "subject"))

# 2. reads run now; writes come back as a token
if not v.candidates:                             # the model named nothing usable: ask the user again
    raise SystemExit("needs clarification")
r = engine.request("agent", v.candidates[0], v.arguments)
if r.get("state") == "proposed":
    show_to_a_human(r["token"])

# 3. a human confirms; the stored arguments run exactly once
print(engine.confirm("alice", r["token"]))

# 4. prove it
assert engine.audit.verify()["ok"]
```

## The four parts

The principles behind them are in [CONSTITUTION.md](CONSTITUTION.md).

| Part | Modules | What it does |
|---|---|---|
| **Constitution** (policy) | `oathline.capabilities`, `oathline.validator` | The frozen allowlist of capabilities and grants (`Capability`, `Registry`, `authorize()`), plus `validate()`, the pure, deterministic filter for model output |
| **Approvals** | `oathline.engine` | `Engine`: request -> authorize -> propose -> human confirm -> execute -> verify, all audited; a principal on the non-human list can't confirm, and with an `approver_check` set `confirm()` also needs a proof. Agents can `propose()` a gated action; it is confirmed under a human's name (a named `approver`, or a human granted the capability), and it then runs once as the agent's action on that human's authority |
| **Tokens** | `oathline.tokens` | `TokenStore`: propose/confirm with TTL (30-3600 s), integrity hash and single use |
| **Audit** | `oathline.audit` | `AuditLog`: append-only SQLite hash chain with `verify(expected_head=...)` |

## Agent proposes, human confirms

```python
from oathline import Capability, Engine, Registry

reg = Registry([Capability("site.deploy", frozenset({"builder-agent", "alice"}), writes=True,
                           needs_confirmation=True, approvers=frozenset({"alice"}))])
engine = Engine(reg, non_human=frozenset({"builder-agent"}))
engine.register("site.deploy", lambda a: {"deployed": a["version"], "by": a["_principal"],
                                          "approved_by": a["_confirmed_by"]})
p = engine.propose("builder-agent", "site.deploy", {"version": "1.5.0"})   # nothing runs
print(engine.confirm("alice", p["token"]))   # runs once; the executor sees _principal=builder-agent, _confirmed_by=alice
```

The audit chain records `PROPOSED_BY_AGENT`, `CONFIRMED_BY_HUMAN` (or `CONFIRM_REFUSED`), `EXECUTING` and
`EXECUTED` with both principals. See `examples/agent_proposes.py`.

In the block above, `"alice"` is only a name: any code that calls `confirm("alice", token)` confirms.
To require more than a name, give the engine an approver check. `confirm()` then needs a proof,
and your function decides whether it is good. The function is called with the principal, the proof,
and `action`: the token id, the capability and the stored integrity hash (a SHA-256 over proposer,
capability and arguments) of exactly what is being confirmed. `engine.action(token)` returns the
same three values, so the code that produces a proof can bind it to one action. In this example the
proof is an HMAC of those values under a key the agent does not hold:

```python
import hashlib, hmac
from oathline import Capability, Engine, Registry

ALICE_KEY = b"demo-key-not-a-real-secret"       # held by alice's approval screen, not by the agent

def proof_for(action):
    message = f"{action['token']}|{action['capability']}|{action['arguments_hash']}".encode()
    return hmac.new(ALICE_KEY, message, hashlib.sha256).hexdigest()

def approver_check(principal, proof, action):   # your code
    return principal == "alice" and isinstance(proof, str) and hmac.compare_digest(proof, proof_for(action))

reg = Registry([Capability("site.deploy", frozenset({"builder-agent", "alice"}), writes=True,
                           needs_confirmation=True, approvers=frozenset({"alice"}))])
engine = Engine(reg, non_human=frozenset({"builder-agent"}), approver_check=approver_check)
engine.register("site.deploy", lambda a: {"deployed": a["version"]})
small = engine.propose("builder-agent", "site.deploy", {"version": "1.5.0"})["token"]
big = engine.propose("builder-agent", "site.deploy", {"version": "2.0.0"})["token"]
proof = proof_for(engine.action(small))                    # alice approves the small one only
print(engine.confirm("alice", small))                      # refused: approver_proof_required
print(engine.confirm("alice", big, proof=proof))           # refused: approver_proof_invalid (a proof for another action)
print(engine.confirm("alice", small, proof=proof))         # runs once
```

```
{'ok': False, 'error': 'approver_proof_required'}
{'ok': False, 'error': 'approver_proof_invalid'}
{'ok': True, 'state': 'executed', 'result': {'deployed': '1.5.0'}, 'verified': None}
```

Anything but `True` from the check, or an error inside it, is a refusal, and each refusal is
recorded as `CONFIRM_REFUSED`. The proof itself is not written to the audit log. The check guards
`confirm()` only: `request()` and `propose()` trust the principal name they are given.

## Secret arguments

Request arguments are written to the audit log. If an argument is a key or a token, name it in the
capability, and the log gets a short hash marker in place of the value:

```python
from oathline import Capability, Engine, Registry

reg = Registry([Capability("volume.list", frozenset({"agent"}), secret_arguments=frozenset({"api_token"}))])
engine = Engine(reg)
engine.register("volume.list", lambda a: {"token_length": len(a["api_token"])})   # the executor gets the real value
print(engine.request("agent", "volume.list", {"api_token": "not-a-real-token"})["result"])
print(engine.audit.events("REQUEST")[0]["payload"])
```

```
{'token_length': 16}
{'arguments': {'api_token': 'secret:sha256:c40bbf978d73'}}
```

The executor still receives the real value, and a confirmation token still binds the real arguments.
A request for a capability that isn't declared has every argument value masked. If the masked arguments
come to more than 8,192 bytes, the log holds only how many arguments there were.

## Reference

**Names.** A principal is the name of whoever is asking: a person or an agent. Your application passes
it in. A capability is the name of one action. A gated capability is one that needs confirmation
(`writes=True` or `needs_confirmation=True`).

**Name lists take plain text.** `non_human=`, `humans=`, the name sets of a `Capability` and
`allowed_arguments=` take plain `str` names only. Anything else in them, such as a member of a `str`-based
Enum, raises `TypeError` when the `Engine` or `Capability` is built, or when `validate()` is called; pass
the Enum member's `.value`.

**Tell the engine who your agents are.** With a plain `Engine(registry)`, these names are non-human and
cannot confirm: `model`, `assistant`, `llm`, `agent`, `system`, `scheduler`, `runner`, `browser`, `gateway`,
`verifier`, `oathline`. Any other name counts as human. If your agent is called `support-bot`, a plain engine
lets `support-bot` confirm its own write. Do one of these:

```python
from oathline import Capability, Engine, Registry
from oathline.engine import DEFAULT_NON_HUMAN

registry = Registry([Capability("email.send", frozenset({"support-bot", "maria"}), writes=True, needs_confirmation=True)])
engine = Engine(registry, humans=frozenset({"maria"}))                      # only maria can confirm; every other name is an agent
engine = Engine(registry, non_human=DEFAULT_NON_HUMAN | {"support-bot"})    # or: add your agent's name to the list
```

`humans=` is the safer of the two: a name you forgot cannot confirm.

**`request()` or `propose()`.** `request(principal, capability, arguments)` is the one call for everything:
a read runs, a gated capability returns a token. `propose(agent, capability, arguments)` is for an agent
asking for a gated action: it refuses a capability that is not gated (`not_gated`) and records who may
approve. For a human's name, `propose()` does the same as `request()`.

**Declared arguments.** A capability can declare the argument names it accepts:
`Capability("email.send", ..., arguments=frozenset({"to", "subject"}))`. `validate()` then keeps those for
that capability without `allowed_arguments=`, and a mistake in the set raises `TypeError` when the
capability is built.

**What `validate()` reads.** A dict with any of: `intent` (one capability name), `candidates` (a list of
capability names), `arguments` (a dict) and `confidence` (a number). Only arguments named in
`allowed_arguments=` are kept; it is empty by default, so by default no argument is kept. A kept value is
text: the number `25` comes back as `"25"`. `v.candidates` is empty when the model named nothing usable, so
check it before using `v.candidates[0]`.

**What the calls return.** Always a dict with `ok`. On success: `state` is `proposed` (with `token`) or
`executed` (with `result` and `verified`). On refusal: `ok` is `False` and `error` is one of the codes below.
`state` is also `failed`, `not_run` or `ran_unrecorded` in the three cases marked.

| Code | Meaning, and what to do |
|---|---|
| `bad_principal` | The principal is not a plain name (empty, too long, spaces at the ends, a character that is not allowed). Fix the name |
| `bad_capability` | The capability name is not a plain name |
| `bad_arguments` | The arguments are not a dict |
| `arguments_not_storable` | The arguments hold something JSON can't (NaN, bytes, a set, mixed key types), or are nested more than 32 deep |
| `arguments_too_large` | The arguments are over 8,192 bytes as JSON |
| `proposal_not_stored` | The token store could not store the proposal. Nothing was proposed |
| `denied` | The capability is not declared, or this principal was not granted it. `message` says which |
| `not_gated` | `propose()` was called for a capability that needs no confirmation. Use `request()` |
| `no_executor` | No executor is registered for the capability. Call `engine.register()` at start-up |
| `non_human_principal` | `confirm()` was called under a name on the non-human list (or an empty or non-text name) |
| `not_a_listed_human` | The engine has a `humans=` list and this name is not on it |
| `unknown_token` | Not a token this store issued |
| `approver_proof_required` | The engine has an approver check and `confirm()` got no `proof` |
| `approver_proof_invalid` | The approver check did not return `True` for this name, proof and action |
| `changed_after_approval` | The stored proposal changed between the approver check and the run. The token is spent |
| `wrong_principal` | A human's own proposal can be confirmed only by that same name. If the proposer was your agent, the engine does not know it is an agent: see "Tell the engine who your agents are" |
| `not_an_approver` | An agent's proposal, confirmed by a name that is not a named approver (or, with none named, not granted the capability) |
| `unknown_capability` | The stored capability is no longer in the registry |
| `already_used` | The token has been confirmed before |
| `expired` | The token's lifetime is over. Make a new request |
| `clock_went_back` | The clock is earlier than when the token was made. The token is kept; fix the clock |
| `clock_error` | The clock did not give a number |
| `tampered` | The stored proposal can be read, and its arguments no longer match its stored hash |
| `corrupt_stored_row` | The stored row cannot be read as a proposal at all (a wrong type in a column, an unusable name, an unknown state). Trying again will not help; make a new request |
| `approver_check_interrupted` | Not returned: the reason logged when the approver check raised `SystemExit` or the like. The error is passed on to your program |
| `no_confirmer` | `TokenStore.redeem()` was called with no confirmer name |
| `token_store_unavailable` | The token store did not answer (locked, closed). Nothing ran; try again |
| `execution_failed` | The executor raised an error. The error text is in the `EXECUTE_FAILED` event in the audit log |
| `execution_returned_failure` | The executor returned `"ok": False`. `state` is `failed`; `result` holds what it returned |
| `audit_write_failed` | The `EXECUTING` record could not be written, so the executor was not called. `state` is `not_run` |
| `ran_but_not_recorded` | The executor ran and its outcome could not be written to the log. `state` is `ran_unrecorded` |

**Reading the audit log.** `engine.audit.events()` returns a list of dicts, oldest first. Each has `seq`,
`ts` (microseconds), `event_type`, `principal`, `action` (the capability name, or `confirm`), `payload`
(a dict) and the hash fields. A principal or capability that was refused as a bad name is logged as the
word `invalid`. The event types are `REQUEST`, `REQUEST_REFUSED`, `AUTHORIZED`, `DENIED`,
`PROPOSED`, `PROPOSED_BY_AGENT`, `CONFIRMED`, `CONFIRMED_BY_HUMAN`, `CONFIRM_REFUSED`, `EXECUTE_REFUSED`,
`EXECUTING`, `EXECUTED`, `EXECUTE_FAILED` and `VERIFIED`. `engine.unfinished()` lists `EXECUTING` events
with no outcome. `engine.mismatched_runs()` lists gated runs whose hash differs from the proposal's, and
tokens that ran more than once.

**Files and lifetimes.** `Engine(registry, audit=AuditLog("audit.db"), tokens=TokenStore("tokens.db", ttl_seconds=300))`
keeps the log and the tokens in SQLite files; the two may be one file or two. `engine.close()` closes both.
`ttl_seconds` is a token's
lifetime, 30 to 3600. `Engine(registry, verifier=fn)` calls `fn(capability, result)` after each run that
succeeded and records its `True` or `False`. It is not called after a run that failed. If its verdict
cannot be written to the log, the result carries `warning: verification_not_recorded`.

## Attacks that fail

[ATTACKS.md](ATTACKS.md) is one table: each attack, the test that proves it fails, and the lines of code
that stop it. If you find one that works, report it privately as [SECURITY.md](SECURITY.md) says.

## What Oathline does not protect against

Every limit we know of, in plain words. If you find one that is not here, it is a bug in this list:
see [SECURITY.md](SECURITY.md).

**Going around it**

- Calls that are not routed through it. An agent holding a raw shell and a raw token goes around it.
- Its own process. Python code running alongside Oathline can replace the registry, the executors, the
  non-human list or the approver check.
- What executors do. Oathline decides *whether* your executor runs, not what it does. It does not undo an
  action, and it does not retry one.

**Who is calling**

- It doesn't authenticate people itself. A principal is a name your application passes in.
- `confirm()` refuses names on the non-human list. That list is only names: a name that isn't on it counts
  as human, and passing `non_human=` replaces the default list. An agent with a name of its own can confirm
  its own write unless you add its name to the list or give the engine a `humans=` list.
- "Writes wait for a human" covers the capabilities you declare with `writes=True` or
  `needs_confirmation=True`. A capability declared with neither runs straight away, whatever its executor
  does.
- Without an approver check, anyone who can call `confirm()` with a human's name is that human, including an
  agent that holds the token.
- With an approver check, Oathline is as strong as your check and no stronger. The proof has to be something
  the agent can't read or replay. Oathline only calls your function and refuses unless it returns `True`.
- The approver check guards `confirm()` only. `request()` and `propose()` trust the principal name they are
  given, so an agent that calls `request("alice", ...)` gets whatever alice was granted: her reads at once,
  and with no approver check her writes too, by confirming under her name.
- Names are compared exactly. `Alice`, and `alice` spelled with a look-alike letter from another alphabet, are
  different names: they get nothing unless someone granted exactly that name. A look-alike of a non-human name
  is not on the non-human list, so it counts as human; it still needs its own grant to do anything.
- A name is refused, not cleaned up, if it has spaces at the ends or more than 128 characters, or holds a
  character that fails any of these five: in ASCII, it is printable; outside ASCII, its Unicode category is
  a letter, mark or digit; it is not on Unicode's default-ignorable list; Python's Unicode table has a name
  for it; and that name does not say blank, filler, joiner, selector, invisible, zero width or mirror. So
  no symbol or punctuation outside ASCII. The rule is `not_allowed_in_a_name` in `oathline/capabilities.py`.
- That rule reads the Unicode tables of the Python that runs it, so a character added in a newer Unicode
  version can be accepted on one Python and refused on another. Some real letters have no name in some
  Python versions and are refused there: on Python 3.12, every Tangut ideograph. It cannot promise how a font draws the
  characters it lets through. What it rests on is exact comparison: a name that only looks like another
  gets nothing unless that exact name was granted.
- Names and arguments must be plain `str` and `dict` objects. Subclasses are refused, in calls and in every
  list of names.

**The two SQLite files**

- Someone who can write to the audit file, or use its connection, can rewrite events from any point to the
  end and recompute their hashes, add new events with correct hashes, cut events off the end, or empty the
  log. `verify()` on its own then passes. It catches the rest: a changed field, a deleted event that is not
  at the end, two swapped events, an event with a wrong hash.
  Keep `audit.head()` outside the machine and pass it to `verify(expected_head=...)`.
- Opening an existing log or token store writes nothing and takes no write lock, so one process can read
  and verify a log while another is writing to it. The table and its triggers are created in one
  transaction, when the table is missing. One exception: a log whose table has no rows and no triggers (a
  creation that was cut short) gets its triggers on open. A log with rows is left exactly as found: if
  someone removed the triggers, opening it does not put them back, and `verify()` does not report it.
- The triggers that refuse UPDATE and DELETE stop accidents. Someone with the file or the connection can
  remove them, or use `INSERT OR REPLACE`; those edits are left to `verify()`.
- Someone who can write to the token store can change a pending proposal together with its hash (the hash
  has no key), mark a used token as unused, or extend an expiry. The changed arguments then run when a human
  confirms. The audit log still shows the original request and still verifies. What gives changed arguments
  away: the log holds the integrity hash at proposal and again at `EXECUTING`, and the two differ. What gives
  a re-used token away: two `EXECUTING` events for one token. `engine.mismatched_runs()` lists both. An
  extended expiry leaves no trace in the log. With an approver check, a change made after the check is refused.
- Token ids are written to the audit log. Someone who can read the log holds every pending token; without
  an approver check, that and a human's name are enough to confirm.
- An approval screen should show the human the stored arguments, not only the hash. Oathline has no screen.
- Both files are plain SQLite, not encrypted. The token store keeps the real arguments of every proposal,
  in clear, and nothing in Oathline deletes old rows.
- An `Engine`, `AuditLog` or `TokenStore` object belongs to the thread that made it (SQLite's rule). Give each
  thread or process its own, pointing at the same files. Two confirmers racing for one token: one wins.

**The record**

- If the audit log can't be written, `request()`, `propose()` and `confirm()` raise an error where they would
  otherwise record a refusal. Nothing runs in that case. If the token store can't be read, `confirm()`
  refuses with `token_store_unavailable`. `engine.action()`, `engine.unfinished()` and
  `engine.mismatched_runs()` read the stores directly and raise the store's own error if it can't be read.
- Something that is not an ordinary `Exception` (`SystemExit`, `KeyboardInterrupt`, `asyncio.CancelledError`,
  `GeneratorExit`) is never swallowed: it reaches your program. Whether it is written to the audit log first
  depends on where it was raised.
  Recorded, then passed on: from an executor (`EXECUTE_FAILED`, and the token is spent); from an approver
  check (`CONFIRM_REFUSED` with reason `approver_check_interrupted`, and the token is not used); from a
  verifier (`VERIFIED` with `verified: false`, after the action has run and been recorded as `EXECUTED`); and
  from the executor's result or error while Oathline looks at it. Oathline looks at them once, under a guard,
  and reads nothing of theirs outside it: whatever they raise, and on whichever touch, exactly one outcome
  event is written for the `EXECUTING` event, and the interrupt is then passed on. When the error's own
  text or class name cannot be read, the record says `unreadable`. The log never holds the result object
  itself, only plain data copied from the one read.
  Passed on without a record: from a clock function you supply, to the token store or to the audit log; and
  from a signal such as Ctrl-C that arrives while Oathline's own code is running rather than yours. If it
  lands inside a log write, that write is undone and the log can be used again. If it lands after an
  executor ran and before its outcome was written, the log ends with `EXECUTING` and `engine.unfinished()`
  lists the action.
  If the log cannot take the record, the interrupt is passed on all the same.
- A token can be spent with nothing run. Once a confirmation has taken the token, a failure before the
  executor starts leaves it used: the log failing, `changed_after_approval`, a grant removed since the
  proposal, or a missing executor. Make a new request.
- If the log fails just after a proposal is stored, `request()` raises and the caller gets no token. The
  proposal row stays in the token store, with no `PROPOSED` event for it, and expires like any other.
  An ordinary `Exception` in an executor, an approver check or a verifier is not passed on: it becomes
  `execution_failed`, `approver_proof_invalid`, or `verified: False`.
- If the process dies while an executor is running, the log ends with `EXECUTING` and no outcome. The token
  is spent and can't be used again. The action may have half-happened; `engine.unfinished()` lists such
  events, and what to do about them is yours.
- Only an explicit `"ok": False` counts as a returned failure: an action that *raises*, or returns a dict with
  `"ok": False`, is recorded as `EXECUTE_FAILED` and `confirm()`/`request()` return `ok: False`. Any other
  result (including a dict without `"ok"`) is recorded as `EXECUTED`; Oathline does not judge what it means.
- A verifier's verdict is reported and recorded. A `False` verdict does not undo the action or change `ok`.
- Async executors are not supported: one that returns a coroutine is recorded as failed and not awaited.
- Principal and capability names are written to the log as given. Don't put a secret in a name.

**Model output**

- `validate()` is not a defence against prompt injection. If the user's text itself contains the attacker's
  value (a pasted email, a web page), that value "appears in the user's text" and is kept.
- The argument check works on words, exactly like this. The user's text is split into words at white space
  of any kind, as Python's `str.split()` does it: spaces, tabs, line breaks, no-break and other Unicode
  spaces, and the separator control characters U+001C to U+001F. A zero-width space does not split. A value is kept when it equals
  one word, or several in a row, after setting aside `"` `'` `(` `[` `{` `<` before it and
  `"` `'` `)` `]` `}` `>` `.` `,` `;` `:` `!` `?` after it. Any white space between words matches any other.
  So it keeps:
  one word out of a longer phrase (`Jane` out of `Jane Citizen`, `cancel` out of `don't cancel`);
  a piece the user wrote with a space in it (`1` out of `1 000,50`, `1111` out of a card number written in
  groups, `/home/bob/my` out of a path with a space);
  and a word without its closing punctuation (`U.S` out of `U.S.`).
  It refuses a piece cut out of one word (`1` out of `1,000.50`, `100` out of `$100`, `b` out of `a.b.c`).
  It checks that the words are the user's, not what the user meant by them. It applies no Unicode
  normalisation.
- Only the eleven field names listed at the top are reported. Nested fields are not inspected.
- It looks at no more than 100 candidates, 100 arguments and 100,000 characters of user text. Over a limit,
  nothing of that kind is kept: no candidate (not even the intent), or no argument.

**Secrets**

- Only the arguments named in `secret_arguments` are masked in the audit log, and only at the top level:
  a secret inside a nested object is logged as it is. When the masked arguments are over 8,192 bytes, the
  log holds only a count of them, so that request's argument names and values are not in the log at all. What an executor returns or raises is logged as it
  is, so an executor that echoes a secret puts it in the log.
- The marker is 12 hex characters of an unsalted SHA-256, and the integrity hash logged for a gated action is
  a full SHA-256 over proposer, capability and arguments. A short or guessable secret can be found from
  either by guessing.

**Time, size and load**

- Expiry uses the clock you give the token store. A token lives for its lifetime, the last instant included.
  A clock earlier than the token's creation is refused, and a token once refused as `expired` stays expired.
  A clock set back to inside the lifetime, before any confirmation has been refused as `expired`, makes the token live again.
  A confirmation refused for another reason (a wrong name, a missing proof) does not mark the token as expired.
- Arguments over 8,192 bytes as JSON are refused, and so are arguments nested more than 32 levels deep and
  arguments JSON can't hold (NaN, Infinity, bytes, sets). Arguments are stored and run in the form JSON reads them back: number keys become text.
- There is no rate limit. An agent can fill the audit log and the token store with requests.

## Next

None of this exists in the repository today.

- **Planned, not built:** a gateway that puts Oathline in front of MCP servers. Reads pass, writes wait for a human, and a tool whose description changes is refused.
- **Planned, not built:** a hook for coding agents that routes destructive commands through approval.
- **Planned, not built:** signed receipts for approved actions.

## Licence

MIT, Copyright (c) 2026 Nexxt Nest Group Pty Ltd (ABN 52 689 862 922). See [LICENSE](LICENSE).
