# Good first issues

Five small, real tasks. Each is one file or two, has a test that says when it is done, and keeps the ground rules of
CONTRIBUTING.md: standard library only, deterministic, fail closed, a test for every behaviour change. Open an issue
with the task's title before starting, so two people do not do the same one.

## 1. `python -m oathline verify <audit.db>`

**What:** a `oathline/__main__.py` with one sub-command, `verify PATH`, that opens the audit log read-only, runs
`AuditLog.verify()` and prints `ok` or the first broken sequence number, exiting 0 or 1. `argparse` only.
**Done when:** `tests/test_cli.py` writes a chain with `Engine`, runs the module in a subprocess on it (exit 0), flips
one byte in a row's `payload` with sqlite3 and runs it again (exit 1, the broken `seq` on stdout). README: one line
under "Reference".

## 2. `why()` on a withdrawn token names the correction

**What:** after `engine.correct()`, the old token is marked expired in the store, so `engine.why(old_token)` and
`confirm()` say `expired`. The `WITHDRAWN` event already carries `new_token`. Make `why()` say
`withdrawn, corrected to <new_token>` when a `WITHDRAWN` event names the token (the refusal code of `confirm()` stays
`expired`: that is what the store says, and the test in `tests/test_hearing.py` pins it).
**Where:** `oathline/engine.py`, `Engine.why`. **Done when:** a test in `tests/test_hearing.py` corrects a proposal
and asserts the line; a token that merely timed out still says `expired`.

## 3. `examples/voice_readback.py`

**What:** the README's hearing block, as a runnable example on the `spoken` channel: a renderer that turns an email
address and an amount into words, a scripted voice product that "speaks" the read-back and records `show` and
`hear`, a yes that runs once. Under 40 lines, no model, no audio.
**Done when:** `tests/test_voice_readback.py` runs it in a subprocess like `tests/test_nine_seconds.py` does and
asserts the printed refusal (`unheard`), the executed state and `chain ok: True`. README: one row in the examples
table.

## 4. Ship the `py.typed` marker

**What:** the package has type hints throughout but no `py.typed` file, so type checkers treat it as untyped. Add
`oathline/py.typed` (empty) and the `package-data` line in `pyproject.toml` so the wheel carries it.
**Done when:** `tests/test_package.py` builds a wheel into a temporary directory with `python -m pip wheel . --no-deps`
and asserts `oathline/py.typed` is in the archive (skip the test when pip is unavailable). MANIFEST.in updated.

## 5. CHANGELOG codes are real

**What:** `tests/test_docs.py` already checks that the README's reference table lists every refusal code the package
can return, and nothing else. Do the same for `CHANGELOG.md`: every refusal code and event type it names in backticks
exists in the code.
**Where:** `tests/test_docs.py`, a new test next to `ReferenceTest`. **Done when:** the test passes on the current
CHANGELOG and fails when a made-up code is added to it (try it, then take it out).
