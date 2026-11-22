#!/usr/bin/env python3
"""Quantum Time Sync — photon-sampling round (one light round).

The census trusts each node's daemon to report its own offset against
its own network server. Sampling does not: it exchanges timestamps
directly with the node and measures. One sample is a Cristian exchange:

  t1  collector clock, just before the probe is sent
  tr  the node's clock, read on the node (`date +%s.%N`)
  t4  collector clock, just after the reply arrives

  offset(node - collector) ~= tr - (t1 + t4) / 2
  delay = t4 - t1        (round trip, including ssh + process spawn)
  error bound = +/- delay / 2

Per node we take several exchanges and keep the minimum-delay one —
the least queueing-contaminated sample, the standard min-delay filter.
On this fleet's WiFi the bound lands in the tens of milliseconds:
sampling is the independent witness that catches gross disagreement
and lying self-reports; it is not a millisecond instrument, and this
script's output says so in its own atoms (bound_ms per node).

Read-only: the probe runs `date` on the node and changes nothing.
Sequential, with pauses — human pace, same ssh recipe as census.py.

Usage: python3 sample.py [--fleet fleet.txt] [--register register.txt]
                         [--samples samples.txt] [--rounds 3]
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

from consensus import parse_register, fnum

SSH_KEY = "/home/hatch/.ssh/id_ed25519"
EE_ANCHOR = 1795327200
EE_QUANTUM_S = 420


def ee_of(unix_ts):
    if unix_ts < EE_ANCHOR:
        return 0
    return int((unix_ts - EE_ANCHOR) // EE_QUANTUM_S) + 1


def exchange(ip):
    """One Cristian exchange. Returns (offset_ms, delay_ms) or None."""
    cmd = ["ssh", "-i", SSH_KEY, "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
           "-o", "BatchMode=yes",
           "-o", "ProxyCommand=nc -X connect -x hatch-egress-proxy:3130 %h %p",
           f"wbic16@{ip}", "date +%s.%N"]
    t1 = time.time_ns()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return None
    t4 = time.time_ns()
    if r.returncode != 0:
        return None
    try:
        sec, _, frac = r.stdout.strip().partition(".")
        tr_ns = int(sec) * 1_000_000_000 + int((frac + "000000000")[:9])
    except ValueError:
        return None
    offset_ms = (tr_ns - (t1 + t4) / 2) / 1e6
    delay_ms = (t4 - t1) / 1e6
    return offset_ms, delay_ms


def collector_state():
    """This machine's own time discipline, for the round's provenance."""
    host = subprocess.run(["hostname"], capture_output=True,
                          text=True).stdout.strip()
    out = subprocess.run(["timedatectl", "timesync-status"],
                         capture_output=True, text=True).stdout
    import re
    ppm = re.search(r"Frequency:\s*([+-]?[\d.]+)\s*ppm", out)
    off = re.search(r"Offset:\s*([+-]?[\d.]+)\s*(ms|us|µs|s)\b", out)
    return host, (ppm.group(1) if ppm else "none"), \
        (off.group(1) + off.group(2) if off else "none")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", default="fleet.txt")
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--samples", default="samples.txt")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    fleet = {}
    with open(args.fleet) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                name, ip = line.split()
                fleet[name] = ip

    blocks = [b for b in parse_register(args.register)
              if not b["superseded"]]
    latest = blocks[-1] if blocks else None
    census_off = {}
    targets = []
    if latest:
        for name, atom in latest["nodes"].items():
            if atom.get("reachable") == "yes" and name in fleet:
                targets.append(name)
                census_off[name] = fnum(atom, "offset_ms")
    targets.sort()

    host, c_ppm, c_off = collector_state()
    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    lines = [f"sampling {stamp} ee={ee_of(now)} collector={host} "
             f"collector_ppm={c_ppm} collector_offset={c_off} "
             f"rounds={args.rounds} method=cristian-min-delay"]
    print(lines[0], flush=True)

    results = {}
    for i, name in enumerate(targets):
        if i:
            time.sleep(1)
        best = None
        for r in range(args.rounds):
            if r:
                time.sleep(0.5)
            got = exchange(fleet[name])
            if got and (best is None or got[1] < best[1]):
                best = got
        if best is None:
            lines.append(f"sample node={name} ip={fleet[name]} ok=no")
            print(lines[-1], flush=True)
            continue
        off, delay = best
        results[name] = (off, delay)
        lines.append(
            f"sample node={name} ip={fleet[name]} ok=yes "
            f"offset_vs_collector_ms={off:.3f} best_delay_ms={delay:.3f} "
            f"bound_ms={delay / 2:.3f}")
        print(lines[-1], flush=True)

    # Cross-check: sample offset (vs collector) minus census offset (vs
    # the node's own network server) should cluster near a common value
    # (collector minus network time) if self-reports and samples agree.
    deltas = {n: results[n][0] - census_off[n]
              for n in results if census_off.get(n) is not None}
    summary = ""
    if deltas:
        vals = sorted(deltas.values())
        med = vals[len(vals) // 2] if len(vals) % 2 else \
            (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
        worst_name = max(deltas, key=lambda n: abs(deltas[n] - med))
        summary = (f"crosscheck nodes={len(deltas)} "
                   f"median_delta_ms={med:.3f} "
                   f"max_deviation_ms={abs(deltas[worst_name] - med):.3f} "
                   f"max_deviation_node={worst_name}")
        lines.append(summary)
        print(summary, flush=True)
    lines.append("end-sampling")
    with open(args.samples, "a") as f:
        f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
