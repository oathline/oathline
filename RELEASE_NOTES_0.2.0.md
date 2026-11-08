# Oathline 0.2.0: the hearing rule

**An AI agent's yes means exactly what the person saw or heard.**

0.1.2 bound a human's yes to the exact arguments of an action and ran it once. It could not say what the person was
shown before saying yes. 0.2.0 adds that, for any capability that asks for it: nothing is written unheard.

## What is new

- **`Capability(..., heard=True, products=frozenset({...}))`** declares a capability that runs only after the person
  was shown exactly what will be written, had it, and said yes to the items as they stand at that moment. `products`
  names the actors that may say so (the voice, the card, the screen); a model actor never can.
- **Items.** A proposal carries `items`: what will be written, as `{key, value}` with an optional `label`, `kind`
  (`"list"`), `shown_as` (`spoken`, `screen`, `kept`, `preset`) and `whole_word`. The words an item is said in are
  Oathline's, rendered by `engine.register_renderer(capability, fn)`; a text supplied by the proposer is refused.
- **`engine.show(product, token, text, channel=)`** is the product's statement of the exact text the person got, by
  voice, on a screen or as a card. It is refused unless every item is in that text, as whole words.
- **`engine.hear(product, token, channel=)`** is the product's statement that the person had the text (spoken to the
  end, the card delivered).
- **`engine.confirm()`** of a heard capability refuses `unheard` and `stale_hearing`. The yes records the hearing it
  relied on (`items_hash`, `heard_seq`, `text_sha256`).
- **`engine.correct(product, token, key, value)`**: the person's correction before the yes is a new proposal with its
  words rendered again, to be shown and heard again; the old token is withdrawn and linked. A corrected key that is
  also an argument runs corrected: the person never hears one value while another runs.
- **`engine.why(token)`** says in one line why a token did not run. **`engine.unheard_runs()`** lists runs of a heard
  capability with no hearing before them: the one number a reviewer needs.
- **Lifetimes up to 30 days** (`ttl_seconds` 30 to 2,592,000; the default stays 300 seconds): a proposal shown on a
  card waits for a yes that may come days later. A proposal past its lifetime is dead the moment anything touches it.
- New events `SHOWN`, `SHOW_REFUSED`, `HEARD`, `HEAR_REFUSED`, `WITHDRAWN`, `CORRECT_REFUSED`; new refusal codes in
  the README's reference table.

## What is unchanged

- A capability that does not declare `heard` behaves exactly as in 0.1.2: no new refusal, no new event. The 250
  tests of 0.1.2 pass unchanged.
- The token integrity hash and the audit schema (version 1) are unchanged. A 0.1.2 token store and audit log open
  under 0.2.0 (two columns are added on open); a 0.2.0 store and log open under 0.1.2.
- Standard library only. Python 3.10 or newer. 6 files.

## What it does not promise

Heard is not understood. `show()` and `hear()` are the product's statements; the core cannot see a screen or hear a
speaker. A correction the product fails to take is a wrong value proposed with the person's consent, on the record as
such. The full list is in the README under "What Oathline does not protect against".

## Numbers

318 tests, no network. The attacks that fail, each with its test and code lines, are in `ATTACKS.md`; the hearing
rule is rule 9 of `CONSTITUTION.md`.

## Install and check

```bash
python -m pip install oathline==0.2.0
python -c "import oathline; print(oathline.__version__)"
```

The SHA-256 of each file attached to this release (`oathline-0.2.0.tar.gz`, `oathline-0.2.0-py3-none-any.whl`) is
on the GitHub release page (SHA256SUMS). These notes are the release page's and are not shipped in the package.

Full change list: `CHANGELOG.md`. Try it in ten lines: the top of the README. Try it in sixty:
`python examples/stop_the_delete.py`.
