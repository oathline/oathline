"""`python -m oathline`: read an audit log (0.3.0). Read-only: the log is opened for reading and never created or written.

    python -m oathline events <audit.db> [--since N] [--type EVENT_TYPE]   one JSON line per event (seq > N)
    python -m oathline verify <audit.db> [--since N] [--from-hash H] [--head H]
    python -m oathline anchor --store <audit.db>                            the anchor to keep OUTSIDE the machine: head, seq, events
    python -m oathline verify --store <audit.db> --expected-head H --anchor-seq N   the log checked against that anchor

`verify` prints its report as JSON and exits 0 when it holds, 1 when it does not; 2 when the file cannot be opened.
`--since N` checks from event N on (event N's own content and every later link); `--from-hash` is the hash you kept for event N.
`--expected-head` (the same as `--head`) without `--anchor-seq` must be the CURRENT head; with `--anchor-seq N` it is the head as it
was when event N was the last one: a log that only grew since passes, a truncated or rewritten one fails.
`--store PATH` may be given instead of the positional path.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .audit import AuditLog


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m oathline", description="Read an Oathline audit log (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("events", help="print events as JSON lines")
    ev.add_argument("path", nargs="?")
    ev.add_argument("--store", default=None, help="the audit log (instead of the positional path)")
    ev.add_argument("--since", type=int, default=None, help="only events with seq greater than this")
    ev.add_argument("--type", default=None, help="only this event type")
    ve = sub.add_parser("verify", help="check the hash chain")
    ve.add_argument("path", nargs="?")
    ve.add_argument("--store", default=None, help="the audit log (instead of the positional path)")
    ve.add_argument("--since", type=int, default=None, help="check from this event on")
    ve.add_argument("--from-hash", default=None, help="the hash you kept for the --since event")
    ve.add_argument("--head", "--expected-head", dest="head", default=None,
                    help="the head hash you kept (catches truncation and a full rewrite)")
    ve.add_argument("--anchor-seq", type=int, default=None, help="the seq the kept head was taken at (from `anchor`)")
    an = sub.add_parser("anchor", help="print the anchor to keep outside the machine: head, seq, events")
    an.add_argument("path", nargs="?")
    an.add_argument("--store", default=None, help="the audit log (instead of the positional path)")
    a = ap.parse_args(argv)
    path = a.store or a.path
    if path:
        path = os.path.expanduser(path)                    # FABLE 01:31: cmd and PowerShell pass "~" through unexpanded
    if not path:
        print(json.dumps({"ok": False, "error": "name the audit log: a path or --store PATH"}))
        return 2
    if a.cmd == "verify" and a.from_hash is not None and a.since is None:
        print(json.dumps({"ok": False, "error": "--from-hash needs --since (the event the hash was kept for)"}))
        return 2                                      # FABLE 00:41: a typed hash is never a no-op
    if a.cmd == "verify" and a.anchor_seq is not None and a.head is None:
        print(json.dumps({"ok": False, "error": "--anchor-seq needs --expected-head (the head kept for that seq)"}))
        return 2
    if a.cmd == "verify" and a.head is not None and (len(a.head) != 64 or any(ch not in "0123456789abcdef" for ch in a.head)):
        print(json.dumps({"ok": False, "refused": "argument",
                          "error": "not a head: 64 lower-case hex characters, as `anchor` printed it"}))
        return 2                                      # a typing slip is never reported as a truncated or replaced log
    if not os.path.isfile(path):
        print(json.dumps({"ok": False, "error": f"no such file: {path}"}))
        return 2
    try:
        log = AuditLog(path, read_only=True)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"cannot open: {type(e).__name__}"}))
        return 2
    try:
        if a.cmd == "events":
            for e in log.events(a.type, since_seq=a.since):
                print(json.dumps(e, sort_keys=True, ensure_ascii=False))
            return 0
        if a.cmd == "anchor":
            r = log.verify()
            evs = log.events()
            out = {"ok": bool(r.get("ok")), "head": log.head(), "seq": (evs[-1]["seq"] if evs else None), "events": len(evs),
                   "chain_id": r.get("chain_id") or log.chain_id,
                   "keep": "outside this machine, out of reach of whoever can write the log; it proves the past up to seq"}
            if not r.get("ok"):
                out["error"] = r.get("error")
            print(json.dumps(out, sort_keys=True))
            return 0 if r.get("ok") else 1
        if a.anchor_seq is not None:
            r = log.verify_anchor(a.head, a.anchor_seq)
            if r.get("refused") == "argument":                 # a typing slip: exit 2 (what you typed), never 1 (the log)
                print(json.dumps(r, sort_keys=True))
                return 2
        else:
            r = log.verify(a.head, from_seq=a.since, from_hash=a.from_hash)
            if r.get("refused") == "argument":
                print(json.dumps(r, sort_keys=True))
                return 2
        print(json.dumps(r, sort_keys=True))
        return 0 if r.get("ok") else 1
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
