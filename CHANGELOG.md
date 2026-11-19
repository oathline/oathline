# Changelog

## 0.3.0

What the 0.2.0 adopters (a voice and card product's email and SMS proposals, the MCP guard, the adapters kit) had to build
for themselves, moved into Core. Everything of 0.2.0 is unchanged; a 0.2.0 audit log and token store open and verify under
0.3.0, and a proposal stored by 0.2.0 completes under it (tests/test_compat_020.py, stores written by the v0.2.0 code).

### Reading the log from where you stopped

- Every event already carried a dense `seq` inside its hashed body (0.1.x and 0.2.0 too); what a reader lacked was a way
  to start after the last event it read. `AuditLog.events(event_type=None, *, since_seq=N)` returns the events with
  `seq > N`. `AuditLog.verify(expected_head=None, *, from_seq=N, from_hash=H)` checks event N itself (its payload and
  event hash) and every link after it, trusting only event N's link to the one before; with `from_hash`, event N must
  carry exactly the hash the reader kept. `events` in the report counts the events after N. The report now says what it
  proves, and no more: without an expected head, that the links hold (a rewrite by someone who can write the file is not
  ruled out: check against an anchored head); with one, that the log is the one whose head was kept; never that anyone
  understood it.
- `AuditLog(path, read_only=True)` opens an existing log for reading only (never creates it).
- A read-only command line: `python -m oathline events <audit.db> [--since N] [--type T]` (one JSON line per event) and
  `python -m oathline verify <audit.db> [--since N] [--from-hash H] [--head H]` (the report as JSON; exit 0 when it
  holds, 1 when not, 2 when the file cannot be opened). Core had no command line before.
- **Anchor it** (Claude 22:18 Fri 13 Nov, after a review of the hash-chain claim): `python -m oathline anchor --store <audit.db>`
  prints the `head`, `seq` and `events` to keep outside the machine; `AuditLog.verify_anchor(head, seq)` and
  `python -m oathline verify --store <audit.db> --expected-head H --anchor-seq N` fail (exit 1) when the log was cut short
  before event N ("shorter than the anchor") or rewritten up to it with every hash recomputed, both of which `verify()` alone
  passes; a log that only grew since the anchor passes. `--expected-head` is `--head`; `--store` may replace the path.
  A head that is not 64 lower-case hex characters, or a seq below 0, is refused as an argument (exit 2, one JSON line)
  before the log is read: a typing slip is never reported as a rewritten log; a head that does not match says both
  readings (the log was rewritten up to it, or it is not the head kept for that seq).
  The same for `--from-hash` / `verify(from_hash=)`; a store path written with `~` is expanded by the command line itself
  (cmd and PowerShell do not); "shorter than the anchor" and a plain head mismatch each also name the other reading (a
  seq typed too high; a log that only grew since the head was kept).

### Items carried in a form that is not their bytes

- An item may be `carried`: `"gutter"` (the text as a block with every line marked `| `, every kind of line break, so
  no line of a message can read as a line of the card around it) or `"hash"` (the SHA-256 of the value's canonical JSON,
  for a value too long to show whole). The capability declares which keys may be carried which way
  (`Capability.gutter_keys`, `Capability.hash_keys`); the proposer marks the item and chooses nothing else. Core renders
  the carried form (`engine.carried_form`), never the proposer and never the product's renderer; a carried item must BE
  one of the call's arguments, same key and an equal value (`carried_not_argument`).
- `show()` renders every carried form again from the STORED argument and refuses the showing when it differs
  (`carried_hash_mismatch`, `carried_mismatch`): the hash a person saw is computed by the thing that records it, not by
  the thing that asks. `correct()` renders a carried item again for its new value, and the argument changes with it.
- The items hash covers `carried` only when an item has it, so every 0.2.0 item hashes exactly as before.
- **`carried: "block"`** (Claude 17:40 Wed 11 Nov: folded into 0.3.0, which is unreleased): for a value too long for the 8 KB
  arguments. The ARGUMENT is `{"sha256", "chars"}` (`Capability.block_keys` declares which keys may be); the product shows the value
  whole as a block under "<label or key>:", every line "| ", closed by "--- end of <key> ---"; `show()` unmarks it and refuses unless
  its SHA-256 and length are the argument's (`carried_hash_mismatch`; no block or an unmarked line: `item_not_in_text`). Core's words
  for the item: "<chars> characters, sha256 <hex>". This is the Guard's long-value binding and SIYA's card hash, moved into Core.
  A block is bytes-faithful: the shown text is split on "\n" only and nothing inside the block changes, so a value holding "\r\n"
  (a file written on Windows) shows and runs; a text whose line breaks a channel turned into "\r\n" is refused. One block per label:
  a shown text in which the label line or the end mark occurs twice is `item_not_in_text` (FABLE 19:00 / 19:06 Thu 12 Nov).

### What runs must have been shown

- `Capability.must_show` (heard capabilities only) names the arguments that must be shown as items, same key and an equal
  value, whenever the call holds them. A proposal that leaves one out is refused at `request()` / `propose()` before
  anything is stored (`unshown_argument`, with `key`), and `confirm()` checks it again before anything runs. An argument
  outside `must_show` may go unshown, as in 0.2.0. `gutter_keys`, `hash_keys` and `must_show` on a capability that is
  not heard are a configuration error at the Registry.

### Unchanged

- The audit schema (version 1), the token integrity hash, every 0.2.0 refusal and event. Standard library only;
  Python 3.10 or newer. 7 files (the new one is the command line).

## 0.2.0

The hearing rule: nothing is written unheard. Opt-in by capability; everything of 0.1.2 is unchanged.

### What 0.1.2 could not say

- A confirmation token was bound to the exact ARGUMENTS of an action by its integrity hash, confirmed once, by a
  human, and recorded. It said nothing about what the human was SHOWN before saying yes: an approval card, a
  read-back spoken by a voice product. The engine could not tell a yes to the right words from a yes to no
  words at all, or a yes given before the proposal was changed under it.

### What changed

- `Capability(..., heard=True, products=frozenset({...}))` declares a gated capability that runs only after
  the human was shown exactly what will be written, had it, and said yes to the items as they stand at that
  moment, and names the product actors (the voice, the card, the screen) that may say what the human was shown
  and that the human had it; a model actor (model, assistant, llm, agent, system, scheduler, runner, browser,
  gateway, verifier, oathline, in any case or width) can never be one, and a heard capability with no product
  is refused at the Registry. The default `heard=False`: a capability that does not declare it meets no new
  refusal, writes no new event, and the 250 tests of 0.1.2 pass unchanged.
- A proposal may carry `items`: what will be written, as `{key, value}` dicts with an optional `label`, `kind`
  (`"list"` for ticks, names, signers), `shown_as` (`spoken`, `screen`, `kept`, `preset`) and `whole_word`
  ("everyone"). The words an item is said in are Core's, never the proposer's: `engine.register_renderer(capability,
  fn)` with `fn(key, value) -> str` (else the value as text; a list as its elements joined by "and") renders every
  item's `said` form when the proposal is made, an item that arrives with a `said` is refused
  (`said_not_allowed`), and a renderer that raises or returns no text refuses the proposal (`said_not_rendered`).
  Items are checked with the arguments' own JSON rules and stored beside them with a hash of their own:
  `items_hash` covers key, value, said, kind, shown_as, label and whole_word, every field the showing check reads,
  so the same value in other words, on another channel, as a list or under another label is another proposal and
  a rewrite of any of them is `tampered`. The integrity hash of 0.1.2 is unchanged, so a 0.1.2 token store opens
  under 0.2.0 (two columns added on open) and a 0.2.0 store opens under 0.1.2. A capability declared `heard=True`
  is refused `items_required` without items.
- `Engine.show(principal, token, text, channel=)`: the product's statement of the exact text the human got,
  accepted only from a name in the capability's `products` (`not_the_product` otherwise). It is refused unless
  every item is in that text: a spoken or preset item by its `said` form, else its value, as
  whole words (`validator.appears_verbatim`, the same function the model's arguments pass through); a list item
  element by element or by its declared whole word; a screen item by its label, on a `screen` or `card` channel
  only; a kept item by its label. The `SHOWN` event holds the token, the items hash, the text's SHA-256, its
  length and the channel. The text itself is in the log only when the engine is built with
  `record_shown_text=True`, and then up to 4,096 characters (a longer one leaves a 512-character preview): a
  governance log is private by default, and a test proves the default log holds no shown text.
- `Engine.hear(principal, token, channel=)`: the product's statement that the human had the shown text
  (spoken to the end, the card delivered), accepted only from the product. `HEARD` holds the items hash and the
  shown text's SHA-256. Refused before any showing (`not_shown`) and when the items are no longer the shown ones
  (`stale_showing`). A SHOWN or HEARD written into the log under any other name counts for nothing: the showing
  hear() needs, the hearing confirm() needs and the hearings unheard_runs() counts are the product's only.
- `confirm()` of a heard capability refuses `unheard` (no HEARD for the token) and `stale_hearing` (the HEARD
  names other items than the proposal holds now). Neither spends the token. `CONFIRMED` and
  `CONFIRMED_BY_HUMAN` record `items_hash`, `heard_seq` and `text_sha256`; `EXECUTING` records `items_hash`.
- `Engine.correct(principal, token, key, value)`: the human's correction before the yes is a NEW proposal
  with that item changed and its words rendered again by Core, which must be shown and heard again; the old
  token is withdrawn and linked (`WITHDRAWN` with `new_token`; the new `PROPOSED` names `corrected_from`).
  When the corrected key is also one of the proposal's ARGUMENTS, the new proposal's argument carries the new
  value and its `arguments_hash` is new (`TokenStore.revise(..., arguments=)`): the human never hears one value
  while another runs.
- A token's lifetime may be up to 30 days (`ttl_seconds` 30 to 2,592,000; it was 30 to 3,600): a proposal shown
  on a card waits for the human's yes, which can come days later. The default stays 300 seconds; nothing else about expiry changes.
- A proposal past its lifetime is dead: `TokenStore.expire_if_due` marks it expired the moment a showing, a
  hearing, a correction or a confirmation reaches it (`expired`); a hearing dies with its proposal.
- The proposer chooses neither the word a list hides behind nor how an item is shown: `whole_word` must be one of
  Core's (`everyone`, `everybody`, `all of them`, `all of you`, `the whole crew`, `the whole team`, `the lot`,
  `all present`; `whole_word_not_allowed` otherwise), and a `screen`, `kept` or `preset` showing is allowed only for
  keys the capability declares in `screen_keys`, `kept_keys` or `preset_keys` (`showing_not_declared` otherwise).
- `Engine.why(token)`: one plain line for "why didn't it run". `Engine.unheard_runs()`: runs of a capability
  declared heard now with no hearing of their items before them (a run from before the flag was set shows up).
- `appears_verbatim` is exported. New event types: `SHOWN`, `SHOW_REFUSED`, `HEARD`, `HEAR_REFUSED`,
  `WITHDRAWN`, `CORRECT_REFUSED`. New refusal codes: see the README's table. The audit schema version is
  unchanged (1): a 0.1.2 log verifies under 0.2.0 and a 0.2.0 log under 0.1.2.
- Finding a token's showing or hearing walks the audit log (linear in its length; `unheard_runs()` and
  `mismatched_runs()` always did). Fine at this release's sizes; an index by token is for a later one.
- 67 new tests (tests/test_hearing.py, test_items.py, test_hearing_hardening.py, test_hearing_conformance.py):
  the rule, its refusals, a locked log at show / hear / correct, a crash between the hearing and the yes, 4,000
  random shapes through the four calls, a correction racing twelve confirmations, and a scripted product whose
  log is read with the two counters a grader would use (unheard presses, unnamed marks: 0 / 0). The rows are in
  `ATTACKS.md`; the rule is the Constitution's rule 9.
- The release kit: the README's first screen (the one line, the problem, ten lines that run, how Oathline differs),
  `examples/stop_the_delete.py` (an agent asks to drop production; shown, refused unheard, refused after a correction,
  run once, the log printed; `tests/test_stop_the_delete.py` pins its output), `RELEASE_NOTES_0.2.0.md` (the release
  page's; not in the package: the checksums it points to are the release page's SHA256SUMS), a third issue
  template ("Show us your use"), `.github/DISCUSSION_CATEGORIES.md` and `.github/GOOD_FIRST_ISSUES.md`.
- What the rule does not promise, in the README's limits: heard is not understood; `show()` and `hear()` are
  the product's statements and the core cannot see a screen or hear a speaker; a correction the product fails
  to take is a wrong value proposed with the person's consent, on the record as such.

## 0.1.2

A locked audit log no longer makes the engine raise, and no longer loses a human's confirmation.

### What was wrong in 0.1.0 and 0.1.1

- Every write to a file-backed audit log or token store waited at most 10 seconds for another writer to
  finish. In the twenty-thread race (`tests/test_race.py`) one round makes about 22 writes to the log, one
  after another; on a slow disk the wait ran out, and `confirm()` raised `sqlite3.OperationalError: database
  is locked` straight to the caller. The test run on the `v0.1.1` tag failed that way on GitHub's Windows
  runner with Python 3.12 (three of twenty threads), while the same commit had passed twice minutes earlier.
  The timing was the runner's; the raise was the package's.
- Worse than the raise: when the wait ran out after the token had been marked used and before the `CONFIRMED`
  record was written, the confirmation was lost. Nothing ran, nothing was recorded, and the next
  `confirm()` with that token was refused as `already_used`. The same loss happened when the `EXECUTING`
  record could not be written (`audit_write_failed`, `state: not_run`): the token stayed spent.
- A refusal that could not be recorded (`CONFIRM_REFUSED`, `REQUEST_REFUSED`, `DENIED`, `EXECUTE_REFUSED`)
  raised instead of being returned. In the race, the nineteen losing threads write `CONFIRM_REFUSED`; a thread
  that raised never closed its connections, which is why the temporary folder could not be removed afterwards.
- The 0.1.0 entry below says the three calls "do not raise, for any names, arguments, token or proof they are
  given". That was true of the inputs and silent about the log. The README said that a log that cannot be
  written makes the calls raise. Neither said that a confirmation could be lost.

### What changed

- `request()`, `propose()` and `confirm()` never raise for a log that cannot be written. A refusal is
  returned with `warning: refusal_not_recorded`. A record the call needs before it may go on (`REQUEST`,
  `AUTHORIZED`, `PROPOSED`, `CONFIRMED`, `CONFIRMED_BY_HUMAN`, `EXECUTING`) that cannot be written is refused
  as `audit_write_failed` with `state: not_run`, and nothing is kept: a proposal with no record is withdrawn
  (`TokenStore.withdraw`, it expires), and a confirmed token is given back to `proposed`
  (`TokenStore.release`) so the same confirmation can be made again once the log is free. If the store
  cannot take the token back either, the caller is told (`warning: token_spent`). The executor is never
  called in any of these cases, so a token that is given back has run nothing.
- A token that has been given back can never be one whose action ran. The store has a fourth state,
  `running`, set by the engine right after the `EXECUTING` record is written and before the executor is
  called (`TokenStore.mark_running`); `release()` works from `used` only and refuses `running`, in the store
  itself, so no caller can re-arm a token that ran (found in review on 8 Oct, before any release: the first
  build of 0.1.2 let `release()` re-arm a used token after its action, and the next confirm ran it again).
  If the store cannot mark the token running, the executor is not called, the `EXECUTING` event gets an
  `EXECUTE_FAILED` outcome (`token_not_marked_running`), the token is given back, and the caller gets
  `token_store_unavailable` with `state: not_run`.
- Stated plainly in the README: the log can hold two `CONFIRMED` events for one run (a first attempt whose
  `EXECUTING` write failed, then the attempt that ran; one `EXECUTING` only), and one `confirm()` on a badly
  contended file can wait up to six times 30 seconds before it refuses.
- The wait for another writer is 30 seconds (`BUSY_TIMEOUT_SECONDS` in `oathline/audit.py` and
  `oathline/tokens.py`), up from 10.
- Interrupts (`SystemExit` and the like) inside a log write are rolled back and passed on, as before.
- Nine new tests in `tests/test_lock_failure.py`: a locked log at the moment of confirmation, at a refusal, at
  a proposal, a store that cannot give the token back, twenty confirmers on a log whose writes fail three
  times in ten, release after the action ran (refused, the action ran once), release from `used` only and
  for the same confirmer only, a crash after `EXECUTING` (the token stays `running`), and a store that
  cannot mark the token running (no run). The race test is unchanged and passed 500 rounds in a row on the development machine before
  release. Two rows added to `ATTACKS.md`.
- Version 0.1.2 in `pyproject.toml`, `oathline/__init__.py` and this file.

## 0.1.1

Documentation and packaging only. No change to the package's behaviour; the 0.1.0 guarantees below stand unchanged.

- README: the four links to repository files (`CONSTITUTION.md`, `ATTACKS.md`, `SECURITY.md`, `LICENSE`) are now
  absolute, so they work on the PyPI project page as well as on GitHub. On PyPI they were broken.
- README: a short block at the top: what Oathline is in three lines, the install line, and a ten-line example.
  `tests/test_readme_top.py` runs that block exactly as printed and checks its output, and checks that the README
  holds no relative links.
- Version 0.1.1 in `pyproject.toml`, `oathline/__init__.py` and this file.

## 0.1.0

The first public version. Standard library only, Python 3.10 or newer. Install with `python -m pip install oathline`.

### What is guaranteed

Each line is enforced in code and covered by tests in `tests/`. [ATTACKS.md](ATTACKS.md) names the test and
the lines of code for each one.

- Only declared capabilities run, and only for principals granted them. A built `Registry` has no API to add
  or widen a grant.
- Names are compared exactly. A principal or capability name that is empty, over 128 characters, has a space
  at either end, or holds any character outside printable ASCII and Unicode letters, marks and digits, or a
  character Unicode lists as default-ignorable or names as a blank, filler, joiner or selector, or one with
  no name in the running Python's Unicode table, is refused and the refusal is recorded.
- A capability that writes never runs on the first call. It returns a token. The token runs the stored
  arguments once, expires after its lifetime, and is refused if the stored arguments no longer match the
  stored hash.
- A principal on the non-human list cannot confirm under its own name, in any case or width, or with spaces
  or characters that are not allowed in a name mixed in. With a `humans=` list, only the listed names can
  confirm.
- With an approver check set, `confirm()` needs a proof that your function accepts for that exact action
  (token id, capability, integrity hash). No proof, a wrong proof, a proof for another action, anything but
  `True`, or an error in the check is a refusal.
- Twenty threads, or two processes, confirming one token against file-backed stores: the executor runs once.
- `EXECUTING` is written to the audit log before an executor is called. If that write fails, the executor
  is not called. The outcome is written after, in full or as a size, a hash and a preview.
- `verify()` detects a change to any field of any event, a deleted event that is not the last, two swapped
  events and a wrong hash.
- Arguments are stored, hashed and run in the form JSON reads them back. The same arguments give the same
  hash. Arguments JSON cannot hold, over 8,192 bytes or nested more than 32 deep are refused before a
  proposal exists.
- An argument named in `secret_arguments` is written to the audit log as a hash marker, never in clear.
- `validate()` does not raise on malformed model output. It keeps an argument only if its value is in the
  user's text as whole words, in the same characters and case.
- `request()`, `propose()` and `confirm()` return a refusal, and do not raise, for any names, arguments,
  token or proof they are given.
- Something that is not an ordinary `Exception` (`SystemExit`, `KeyboardInterrupt`, `asyncio.CancelledError`,
  `GeneratorExit`) is never swallowed. Raised by your executor, approver check or verifier, it is written to
  the audit log first (`EXECUTE_FAILED`; `CONFIRM_REFUSED` with reason `approver_check_interrupted`, the
  token not used; or `VERIFIED` false) and then passed on. The executor's result or error is looked at once,
  under a guard, and nothing of it is read outside that guard: whatever it raises, on whichever touch, exactly
  one outcome event is written for the `EXECUTING` event before the interrupt is passed on.
- A capability can declare the argument names it accepts (`arguments=`); `validate()` keeps those for it.
- Every list of names (`non_human=`, `humans=`, a capability's name sets, `allowed_arguments=`) takes plain
  `str` only. Anything else raises `TypeError` when the `Engine` or `Capability` is built, or when
  `validate()` is called.

### Fixed before release

- Opening an existing audit log or token store ran a `CREATE ... IF NOT EXISTS` script, which is a write. When
  the log's triggers were missing, that write needed the file's write lock, so a reader opening the log to
  verify it waited the full 10 seconds and then failed with "database is locked" whenever another connection
  was in the middle of a transaction. In the test suite this showed up as five intermittent errors. And when
  that open failed, the half-opened connection was never closed. Now opening an existing store writes nothing
  and takes no lock: the table and its triggers are created together, only when the file is new; a failed
  open closes its connection. The table and its triggers are created in one transaction; a log left with an
  empty table and no triggers by a cut-short creation gets its triggers on open, and a log with rows is left
  as found. `tests/test_locks.py` holds the proof, including a second process verifying the log while the
  first holds a write transaction. The test suite's own fault that exposed it (a forged
  hash that collided one time in sixteen, leaving a test connection open with a failed transaction) is fixed
  in `tests/test_tamper_sweep.py`.

### Known limits

The full list, in plain words, is the README section
["What Oathline does not protect against"](README.md#what-oathline-does-not-protect-against). In short:

- It only governs calls routed through it, and it does not defend against its own process.
- The three calls can still raise: an error from the audit log when it cannot be written, and an interrupt
  (above). An interrupt raised by a clock function you supply, or by a signal arriving while Oathline's own
  code runs, is passed on without a record; a log write it lands in is undone.
- It does not authenticate people. Without an approver check, a human's name is enough to confirm.
- Someone who can write the audit file can cut events off the end or rewrite the tail; only a head hash kept
  elsewhere catches that. Someone who can write the token store can change a pending proposal.
- A crash mid-action leaves `EXECUTING` with no outcome; Oathline does not undo or retry.
- A clock set back to inside the lifetime, before any confirmation has been refused as `expired`, makes the token live again.
- `validate()` is not a defence against prompt injection.
- There is no rate limit, no encryption of the two SQLite files, and one object per thread.
