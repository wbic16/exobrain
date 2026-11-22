#!/usr/bin/env python3
"""exobrain — round runner.

Runs on the designated runner node (standing: aletheia-core; failover:
any node — all state lives in the shared register, so the same command
run anywhere derives the same results). Once per round, after the
emission window closes, the runner:

  1. ASSEMBLES the previous round's per-node inbox atoms into a census
     block in the shared register.txt — the exact block format the
     VM-era census.py wrote, so every downstream tool works unchanged.
  2. COMPUTES the round: election (elect.py), consensus (consensus.py),
     EMA blend (blend.py), and the time-stream detector (listen.py),
     all against the shared register files.
  3. Once per UTC day, on the first assembly of a new day, runs the
     daily drift cross-reference (drift_check.py) for the completed
     previous day — one drift atom per day, same semantics as the
     VM-era daily cron.

The runner COMPUTES ONLY. It never changes a clock, a daemon, or a
config, on any node.

Invocation: a user systemd timer fires this script every minute with
--tick; assembly happens 360–660 s after a round boundary, once per
round (state in ~/exobrain/state/). Output is appended to
~/exobrain/state/round.log on the runner.

Usage: python3 run_round.py [--tick] [--shared-dir DIR] [--app-dir DIR]
       python3 run_round.py --assemble-now   (assemble latest round now)
"""
import argparse
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

EE_ANCHOR = 1795327200          # EE 1 begins 2026-11-22 00:00:00 CST
ROUND_S = 6 * 420               # one round = 6 EE = 2520 s, EE-aligned
ASSEMBLE_EARLIEST = 360         # assemble 6–11 min after the boundary
ASSEMBLE_LATEST = 660
DEFAULT_SHARED = "/mnt/mirrorborn/shared/exobrain"
GREAT_WHITE = ("node great-white ip=none reachable=no "
               "reason=no-address-in-record-and-key-refused")


def round_of(unix_ts):
    idx = (unix_ts - EE_ANCHOR) // ROUND_S
    return idx, EE_ANCHOR + idx * ROUND_S


def ee_of(unix_ts):
    return int((unix_ts - EE_ANCHOR) // 420) + 1


def read_state(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def write_state(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{value}\n")


def log(app_dir, msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {msg}"
    print(line, flush=True)
    with open(os.path.join(app_dir, "state", "round.log"), "a") as f:
        f.write(line + "\n")


def collect_round_atoms(shared_dir, round_idx):
    """{node: atom_line} for one round from every node's inbox file."""
    inbox_dir = os.path.join(shared_dir, "inbox")
    atoms = {}
    try:
        files = sorted(os.listdir(inbox_dir))
    except OSError:
        return atoms
    prefix = f"atom round={round_idx} "
    for fn in files:
        if not fn.endswith(".txt"):
            continue
        try:
            with open(os.path.join(inbox_dir, fn)) as f:
                for line in f:
                    line = line.rstrip("\n")
                    if line.startswith(prefix):
                        atom = line.split(" ", 5)[-1]
                        if atom.startswith("node "):
                            atoms[atom.split()[1]] = atom  # last wins
        except OSError:
            continue
    return atoms


def assemble(shared_dir, fleet_order, round_idx, round_start):
    atoms = collect_round_atoms(shared_dir, round_idx)
    if not atoms:
        return 0
    stamp = datetime.fromtimestamp(round_start, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    lines = [f"census {stamp} ee={ee_of(round_start)}"]
    for name in fleet_order:
        if name in atoms:
            lines.append(atoms[name])
    for name in sorted(atoms):
        if name not in fleet_order:
            lines.append(atoms[name])
    lines.append(GREAT_WHITE)
    lines.append("end-census")
    with open(os.path.join(shared_dir, "register.txt"), "a") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return len(atoms)


def run_chain(app_dir, shared_dir, do_drift_day=None):
    reg = os.path.join(shared_dir, "register.txt")
    steps = [
        ["elect.py", "--register", reg,
         "--elections", os.path.join(shared_dir, "elections.txt")],
        ["consensus.py", "--register", reg,
         "--consensus-log", os.path.join(shared_dir, "consensus.txt")],
        ["blend.py",
         "--consensus-log", os.path.join(shared_dir, "consensus.txt"),
         "--blend-log", os.path.join(shared_dir, "blend.txt")],
        ["listen.py", "--register", reg,
         "--events", os.path.join(shared_dir, "events.txt"),
         "--detections", os.path.join(shared_dir, "detections.txt")],
    ]
    if do_drift_day:
        steps.append(["drift_check.py", "--register", reg,
                      "--drift-log", os.path.join(shared_dir, "drift.txt"),
                      "--date", do_drift_day])
    for step in steps:
        r = subprocess.run([sys.executable] + [os.path.join(app_dir, step[0])]
                           + step[1:], cwd=app_dir, capture_output=True,
                           text=True, timeout=300)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        log(app_dir, f"{step[0]}: exit={r.returncode} {tail[:160]}")
        # Exit-code dialects: drift_check 2 = exceeds-tolerance (a result,
        # not a failure); listen 3 = new detections appended (a result).
        if r.returncode not in (0, 2, 3):
            err = (r.stderr.strip().splitlines() or [""])[-1]
            log(app_dir, f"{step[0]}: stderr: {err[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--assemble-now", action="store_true",
                    help="assemble the most recent completed round now")
    ap.add_argument("--shared-dir", default=DEFAULT_SHARED)
    ap.add_argument("--app-dir",
                    default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    app_dir = args.app_dir
    state_dir = os.path.join(app_dir, "state")
    os.makedirs(state_dir, exist_ok=True)

    # One runner at a time, even if a failover node fires concurrently.
    lock = open(os.path.join(state_dir, "round.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0

    fleet_order = []
    try:
        with open(os.path.join(app_dir, "fleet.txt")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    fleet_order.append(line.split()[0])
    except OSError:
        pass

    now = int(time.time())
    cur_idx, cur_start = round_of(now)
    last_path = os.path.join(state_dir, "last_assembled.txt")
    last = read_state(last_path, "-1")
    last = int(last) if last.lstrip("-").isdigit() else -1

    if args.assemble_now:
        target = cur_idx - 1
    elif args.tick:
        if not (ASSEMBLE_EARLIEST <= now - cur_start <= ASSEMBLE_LATEST):
            return 0
        target = cur_idx - 1
    else:
        target = cur_idx - 1

    if target <= last:
        return 0
    t_start = EE_ANCHOR + target * ROUND_S
    n = assemble(args.shared_dir, fleet_order, target, t_start)
    write_state(last_path, str(target))
    if n == 0:
        log(app_dir, f"round {target}: no inbox atoms, nothing assembled")
        return 0
    log(app_dir, f"round {target}: assembled census block from {n} node atoms")

    # Daily drift cross-reference for the previous UTC day, once.
    drift_day = None
    today = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    drift_state = os.path.join(state_dir, "last_drift_day.txt")
    if read_state(drift_state) != today:
        yday = (datetime.fromtimestamp(now, timezone.utc)
                - timedelta(days=1)).strftime("%Y-%m-%d")
        drift_day = yday
        write_state(drift_state, today)
    run_chain(app_dir, args.shared_dir, do_drift_day=drift_day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
