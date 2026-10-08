# Changelog

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
