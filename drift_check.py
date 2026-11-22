#!/usr/bin/env python3
"""Quantum Time Sync — daily drift cross-reference.

Will's amendment, 2026-11-22: "cross reference average drift from
network time daily." For one UTC day, this takes every non-superseded
census block stamped that day, computes the trimmed consensus for each
(from consensus.py — the same trim, weights and combination rules),
and averages: the day's average consensus drift versus network time,
and the day's mean consensus offset. The ensemble anchors to network
time in aggregate — every voter's ppm and offset are measured against
its own network NTP source, so the consensus drift IS the fleet's
drift against network time, with no single node's feed trusted.

The cross-reference is appended as one atom to drift.txt. Verdict:
within-tolerance, or exceeds-tolerance if |average drift| > 25 ppm
(about 2.2 s/day of underlying rate — beyond it the ensemble is being
carried by poor clocks and the trim/weights need review) or |mean
consensus offset| > 50 ms (half the 100 ms source-switch guard in
DESIGN.md). Exit code: 0 within tolerance, 2 exceeds, 1 no data.

Usage: python3 drift_check.py [--register register.txt]
                              [--drift-log drift.txt] [--date YYYY-MM-DD]
                              [--no-log]
Default day: the latest day present in the register.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

from consensus import parse_register, compute_consensus, fmt

DRIFT_TOL_PPM = 25.0
OFFSET_TOL_MS = 50.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--drift-log", default="drift.txt")
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    blocks = parse_register(args.register)
    live = [b for b in blocks if not b["superseded"]]
    n_superseded = len(blocks) - len(live)
    if not live:
        print("no usable census atoms found", file=sys.stderr)
        return 1

    day = args.date or live[-1]["stamp"][:10]
    todays = [b for b in live if b["stamp"][:10] == day]
    if not todays:
        print(f"no census atoms for {day}", file=sys.stderr)
        return 1

    per_census = []
    for b in todays:
        res = compute_consensus(b)
        if res is not None:
            per_census.append((b, res))
    if not per_census:
        print(f"consensus could not form in any census of {day}",
              file=sys.stderr)
        return 1

    avg_drift = sum(r["consensus_drift_ppm"]
                    for _, r in per_census) / len(per_census)
    mean_off = sum(r["consensus_offset_ms"]
                   for _, r in per_census) / len(per_census)
    voters = per_census[-1][1]["voters"]
    eligible = per_census[-1][1]["eligible"]
    exceeds = abs(avg_drift) > DRIFT_TOL_PPM or abs(mean_off) > OFFSET_TOL_MS
    verdict = "exceeds-tolerance" if exceeds else "within-tolerance"

    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    print(f"DAILY DRIFT CROSS-REFERENCE — {day} (UTC)")
    print(f"  censuses used: {len(per_census)} "
          f"(superseded blocks excluded: {n_superseded})")
    for b, r in per_census:
        print(f"    census {b['stamp']}: drift "
              f"{fmt(r['consensus_drift_ppm'])} ppm, offset "
              f"{fmt(r['consensus_offset_ms'])} ms, "
              f"voters {len(r['voters'])} of {r['eligible']} eligible")
    print(f"  average consensus drift vs network time: "
          f"{fmt(avg_drift)} ppm = {fmt(avg_drift * 86.4, 1)} ms/day")
    print(f"  mean consensus offset vs network time:   "
          f"{fmt(mean_off)} ms")
    print(f"  tolerance: |drift| <= {DRIFT_TOL_PPM:.0f} ppm and "
          f"|offset| <= {OFFSET_TOL_MS:.0f} ms — verdict: {verdict}")

    if not args.no_log:
        with open(args.drift_log, "a") as f:
            f.write(
                f"drift {stamp} day={day} "
                f"censuses={len(per_census)} "
                f"superseded_excluded={n_superseded} "
                f"avg_drift_ppm={fmt(avg_drift)} "
                f"avg_drift_ms_per_day={fmt(avg_drift * 86.4, 1)} "
                f"mean_consensus_offset_ms={fmt(mean_off)} "
                f"voters={len(voters)} eligible={eligible} "
                f"verdict={verdict}\n")
    return 2 if exceeds else 0


if __name__ == "__main__":
    sys.exit(main())
