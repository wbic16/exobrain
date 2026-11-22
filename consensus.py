#!/usr/bin/env python3
"""Quantum Time Sync — consensus time (Mode 2).

Reads the latest non-superseded census block in register.txt and builds
the fleet's consensus time from the ensemble — no single clock is the
authority. The least accurate 20% of eligible clocks are trimmed (they
are still measured and logged every round; they simply do not vote),
and the survivors vote with weights from their measured stability:
w = 1 / score_ms^2 (inverse-variance weighting on the census score).

Consensus offset  = inverse-variance weighted median of voter offsets.
Consensus drift   = inverse-variance weighted mean of voter ppm — an
                    average rate, which is what the daily cross-reference
                    against network time is defined over.

Consensus needs at least MIN_ELIGIBLE eligible clocks in the census;
below that it does not form and the elected-master mode is the fallback
(DESIGN.md, Mode 2). The result is appended as an atom to consensus.txt
and printed. This script changes nothing on any node; it only computes.

Usage: python3 consensus.py [--register register.txt]
                            [--consensus-log consensus.txt] [--no-log]
"""
import argparse
import math
import re
import sys
import time
from datetime import datetime, timezone

MIN_ELIGIBLE = 5   # consensus cannot form below this many eligible clocks
MIN_VOTERS = 4     # the trim never reduces the voting set below this
TRIM_FRACTION = 0.20


def parse_register(path):
    """Return census blocks: {stamp, ee, nodes{name: atom}, superseded}.

    A block followed (after its end-census line) by a '# note:' line
    saying it was superseded is marked superseded: it stays in the
    register as evidence but never votes.
    """
    blocks = []
    cur = None
    pending_note_check = False
    with open(path) as f:
        for line in f:
            s = line.strip()
            if s.startswith("census "):
                cur = {"stamp": s.split()[1],
                       "ee": int(re.search(r"ee=(\d+)", s).group(1)),
                       "nodes": {}, "superseded": False}
                blocks.append(cur)
                pending_note_check = False
            elif s.startswith("node ") and cur is not None:
                atom = dict(kv.split("=", 1) for kv in s.split()[1:]
                            if "=" in kv)
                atom["node"] = s.split()[1]
                cur["nodes"][atom["node"]] = atom
            elif s == "end-census":
                cur = None
                pending_note_check = True
            elif pending_note_check and s.startswith("#"):
                # Register notes can wrap over several '#' lines; the
                # superseded marker may be on any of them.
                if "superseded" in s and blocks:
                    blocks[-1]["superseded"] = True
            elif pending_note_check:
                pending_note_check = False
    return blocks


def fnum(atom, key):
    v = atom.get(key)
    return None if v in (None, "none") else float(v)


def trim_count(n_eligible):
    """Worst clocks to discard: round-half-up(0.20 * n), capped so at
    least MIN_VOTERS always remain."""
    k = int(math.floor(TRIM_FRACTION * n_eligible + 0.5))
    return max(0, min(k, n_eligible - MIN_VOTERS))


def compute_consensus(block):
    """Consensus for one census block, or None if it cannot form."""
    elig = []
    for name, atom in block["nodes"].items():
        if atom.get("reachable") == "yes" and atom.get("eligible") == "yes":
            elig.append({"node": name,
                         "ppm": fnum(atom, "ppm"),
                         "offset_ms": fnum(atom, "offset_ms"),
                         "jitter_ms": fnum(atom, "jitter_ms"),
                         "score_ms": fnum(atom, "score_ms")})
    if len(elig) < MIN_ELIGIBLE:
        return None
    elig.sort(key=lambda e: e["score_ms"])  # most accurate first
    k = trim_count(len(elig))
    trimmed = elig[len(elig) - k:] if k else []
    voters = elig[:len(elig) - k] if k else elig
    for v in voters:
        v["weight"] = 1.0 / (v["score_ms"] ** 2)
    wsum = sum(v["weight"] for v in voters)

    # Weighted median of offsets: first offset (sorted ascending) at
    # which cumulative weight reaches half the total weight.
    by_off = sorted(voters, key=lambda v: v["offset_ms"])
    half = wsum / 2.0
    cum = 0.0
    cons_off = by_off[-1]["offset_ms"]
    for v in by_off:
        cum += v["weight"]
        if cum >= half:
            cons_off = v["offset_ms"]
            break

    cons_ppm = sum(v["weight"] * v["ppm"] for v in voters) / wsum
    spread = math.sqrt(
        sum(v["weight"] * (v["offset_ms"] - cons_off) ** 2
            for v in voters) / wsum)
    return {"eligible": len(elig), "trimmed": trimmed, "voters": voters,
            "consensus_offset_ms": cons_off,
            "consensus_drift_ppm": cons_ppm,
            "spread_ms": spread}


def fmt(v, nd=3):
    return "none" if v is None else f"{v:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--consensus-log", default="consensus.txt")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    blocks = [b for b in parse_register(args.register)
              if not b["superseded"]]
    if not blocks:
        print("no usable census atoms found", file=sys.stderr)
        return 1
    block = blocks[-1]
    res = compute_consensus(block)
    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    if res is None:
        n = sum(1 for a in block["nodes"].values()
                if a.get("eligible") == "yes")
        print(f"CONSENSUS CANNOT FORM: {n} eligible clock(s) in census "
              f"{block['stamp']}, minimum is {MIN_ELIGIBLE}. "
              "Fallback: elected-master mode.")
        if not args.no_log:
            with open(args.consensus_log, "a") as f:
                f.write(f"consensus {stamp} census={block['stamp']} "
                        f"ee={block['ee']} formed=no eligible={n} "
                        f"reason=below-minimum-{MIN_ELIGIBLE}\n")
        return 0

    trimmed_names = ",".join(t["node"] for t in res["trimmed"]) or "none"
    print(f"CONSENSUS TIME — census {block['stamp']} (EE {block['ee']})")
    print(f"  eligible clocks: {res['eligible']}; trimmed (worst "
          f"{TRIM_FRACTION:.0%}): {len(res['trimmed'])} "
          f"[{trimmed_names}]; voters: {len(res['voters'])}")
    print(f"  consensus offset vs network time: "
          f"{fmt(res['consensus_offset_ms'])} ms (weighted median)")
    print(f"  consensus drift vs network time:  "
          f"{fmt(res['consensus_drift_ppm'])} ppm (weighted mean) = "
          f"{fmt(res['consensus_drift_ppm'] * 86.4, 1)} ms/day")
    print(f"  voter disagreement (weighted std): "
          f"{fmt(res['spread_ms'])} ms")
    print("  voters (weight from 1/score^2):")
    for v in sorted(res["voters"], key=lambda v: v["score_ms"]):
        print(f"    {v['node']:<18} score={fmt(v['score_ms']):>9}  "
              f"ppm={fmt(v['ppm']):>9}  offset_ms={fmt(v['offset_ms']):>9}"
              f"  weight={v['weight']:.6f}")
    if res["trimmed"]:
        print("  trimmed this round (measured, logged, no vote):")
        for t in sorted(res["trimmed"], key=lambda t: t["score_ms"]):
            print(f"    {t['node']:<18} score={fmt(t['score_ms']):>9}  "
                  f"ppm={fmt(t['ppm']):>9}  "
                  f"offset_ms={fmt(t['offset_ms']):>9}")

    if not args.no_log:
        with open(args.consensus_log, "a") as f:
            f.write(
                f"consensus {stamp} census={block['stamp']} "
                f"ee={block['ee']} formed=yes "
                f"eligible={res['eligible']} "
                f"trimmed={len(res['trimmed'])} "
                f"trimmed_nodes={trimmed_names} "
                f"voters={len(res['voters'])} "
                f"offset_ms={fmt(res['consensus_offset_ms'])} "
                f"drift_ppm={fmt(res['consensus_drift_ppm'])} "
                f"spread_ms={fmt(res['spread_ms'])} "
                "rule=trim20-inverse-variance-weighted-median\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
