# Changelog

## 0.1.0

The first public version. Standard library only, Python 3.10 or newer.

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
