# Oathline 0.3.0

**What a person saw is what runs, now down to the rendering.**

0.2.0 made a heard capability run only after the person was shown, and had, exactly what will be written. Its adopters then
each built the same three things themselves: a marked block for a message on a card, a hash for a value too long to show,
and a way to read the log from where they stopped. 0.3.0 puts those in Core.

## What is new

- **Carried items.** An item may be carried as a marked block (`"gutter"`) or as a SHA-256 (`"hash"`). The capability
  declares which keys may be (`gutter_keys`, `hash_keys`); Oathline renders the form from the argument itself; a carried
  item must be one of the call's arguments.
- **`show()` checks carried forms against the stored arguments** and refuses a mismatch, so the hash on the screen is a
  hash of what will run. The hash is computed over the arguments' own canonical JSON, the form the token binds. A bare
  hash is easy to skim past: the product's screen or card must say what it is a hash of (Oathline cannot know what the
  argument means to a person).
- **Blocks.** A value too long to be an argument is carried as its hash, and shown whole as a marked block; `show()` itself
  checks that the block shown hashes to the argument, so "the long text I saw is the one that runs" no longer depends on the product.
  The block is hashed exactly as shown (a "\r" in the value stays in it), and a text that shows two blocks under one label is refused.
- **Anchor the log.** `python -m oathline anchor` prints the head and its sequence number to keep somewhere the log's writer
  cannot reach; `verify --expected-head ... --anchor-seq ...` then catches a log cut short or rewritten up to that point, even
  with every hash recomputed. An anchor proves the past up to its point, nothing after it.
- **`must_show`.** A capability names the arguments that must be shown. A proposal that leaves one out is refused before
  it is stored, and again before it runs.
- **Reading from a sequence number.** `events(since_seq=N)`, `verify(from_seq=N, from_hash=H)`, and a read-only command
  line: `python -m oathline events|verify <audit.db> --since N`.

## What is unchanged

- Everything of 0.2.0. A 0.2.0 audit log and token store open, verify and complete their proposals under 0.3.0.
- The audit schema and the token integrity hash. Standard library only. Python 3.10 or newer.

## What it does not promise

- It proves what was displayed and what was bound to the yes, not that the person understood it.
- It cannot see redirects or connections an adapter makes: destinations are the adapter's to check (the adapters kit
  ships a no-redirect opener and a destinations check).

The SHA-256 of each file attached to this release is on the release page, not in the package.
