#!/usr/bin/env python3
"""exobrain — per-node time agent.

Runs on every fleet node. Once per round (a round is 6 Exocortical
Epochs = 42 minutes, and every round starts exactly on an EE boundary),
the agent measures its OWN clock — the same probe the VM-era census ran
over ssh (census.py's REMOTE script + parser, reused verbatim) — and
appends one atom to its own inbox file on the shared register:

    /mnt/mirrorborn/shared/exobrain/inbox/<node>.txt

Only the owning node ever writes its inbox file, so there is no
cross-node write contention. If the share is unreachable, the atom goes
to a local spool (~/exobrain/state/spool.txt) and is flushed on a later
tick when the share returns.

The agent MEASURES ONLY. It never changes a clock, a daemon, or a
config. Sync/apply remains a deliberate act (apply_sync.py).

Invocation: a user systemd timer fires this script every minute with
--tick; the script emits at most one atom per round, inside the first
300 seconds of the round, and is otherwise a no-op. State (last emitted
round) lives in ~/exobrain/state/ so a reboot or a missed tick cannot
double-emit.

Usage: python3 node_agent.py [--tick] [--shared-dir DIR] [--app-dir DIR]
       python3 node_agent.py --now   (emit immediately, for install verify)
"""
import argparse
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from census import REMOTE, parse_probe, score_ms, atom_line, ee_of  # noqa: E402

EE_ANCHOR = 1795327200          # EE 1 begins 2026-11-22 00:00:00 CST
ROUND_S = 6 * 420               # one round = 6 EE = 2520 s, EE-aligned
EMIT_WINDOW_S = 300             # emit inside the first 300 s of a round
DEFAULT_SHARED = "/mnt/mirrorborn/shared/exobrain"


def round_of(unix_ts):
    """(round_index, round_start_unix, round_ee) for a timestamp."""
    idx = (unix_ts - EE_ANCHOR) // ROUND_S
    start = EE_ANCHOR + idx * ROUND_S
    return idx, start, ee_of(start)


def measure_local(name):
    """Run census.py's probe locally and shape the result like probe_node."""
    try:
        r = subprocess.run(["bash", "-s"], input=REMOTE, capture_output=True,
                           text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"node": name, "ip": "self", "reachable": False,
                "reason": "local-probe-timeout"}
    if r.returncode != 0 or "===END===" not in r.stdout:
        return {"node": name, "ip": "self", "reachable": False,
                "reason": f"local-probe-exit-{r.returncode}"}
    d = parse_probe(r.stdout)
    d.update({"node": name, "ip": "self", "reachable": True})
    d["eligible"] = bool(d["synced"] and d["ppm"] is not None
                         and d["jitter_ms"] is not None
                         and d["offset_ms"] is not None)
    if d["eligible"]:
        d["score_ms"] = score_ms(d["ppm"], d["jitter_ms"], d["offset_ms"])
    return d


def read_state(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return -1


def write_state(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def append_line(path, line):
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", action="store_true",
                    help="emit only if inside a round's emission window")
    ap.add_argument("--now", action="store_true",
                    help="emit immediately regardless of window (verify)")
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED)
    ap.add_argument("--app-dir",
                    default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    app_dir = args.app_dir
    state_dir = os.path.join(app_dir, "state")
    os.makedirs(state_dir, exist_ok=True)

    name_file = os.path.join(app_dir, "node-name")
    try:
        with open(name_file) as f:
            node = f.read().strip()
    except OSError:
        node = socket.gethostname()

    now = int(time.time())
    idx, start, ee = round_of(now)
    last_path = os.path.join(state_dir, "last_round.txt")
    last = read_state(last_path)

    if args.tick and not args.now:
        if now - start > EMIT_WINDOW_S:
            return 0            # outside this round's emission window
        if last >= idx:
            return 0            # already emitted for this round

    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    d = measure_local(node)
    line = (f"atom round={idx} round_start={start} ee={ee} stamp={stamp} "
            + atom_line(d))

    inbox = os.path.join(args.shared_dir, "inbox", f"{node}.txt")
    spool = os.path.join(state_dir, "spool.txt")
    delivered = False
    try:
        os.makedirs(os.path.dirname(inbox), exist_ok=True)
        # Flush any spooled atoms first, then this round's atom.
        if os.path.exists(spool):
            with open(spool) as f:
                pending = [ln.rstrip("\n") for ln in f if ln.strip()]
            for ln in pending:
                append_line(inbox, ln)
            os.unlink(spool)
        append_line(inbox, line)
        delivered = True
    except OSError:
        append_line(spool, line)
    write_state(last_path, idx)
    print(f"{'appended' if delivered else 'spooled'}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
