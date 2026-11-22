#!/usr/bin/env python3
"""Quantum Time Sync — the EMA blender (managed consensus time).

Will's amendment, 2026-11-22 10:43 CST: "manage consensus time with an
EMA blender to stay tracked to network time."

The raw consensus (consensus.py) is one statistic per census round, and
it is noisy: the fleet's per-node jitter runs 10–250 ms and a single
round's voter disagreement measured 18.390 ms. It also wanders: the
first round's consensus drift was −12.084 ppm, about −1 s/day of
underlying rate, which no one should chase round by round. The blender
turns the raw consensus into the *managed* consensus — the timescale
the fleet actually steers by — in two moves per round:

1. Blend toward network time. Each round's target is a weighted blend
   of the raw fleet consensus and network time itself:

       target = GAMMA * raw + (1 - GAMMA) * network

   Network time is the zero of the measurement frame (every voter's
   ppm and offset are measured against its own network NTP source),
   so network contributes offset 0 and drift 0. GAMMA = 0.5: fleet
   evidence and the network anchor weigh equally in the target. The
   fleet's own measurement is never discarded (that would be Mode 1
   trust again, in the network), and the network never simply rules
   (that would discard the ensemble Will asked for).

2. Smooth with an EMA. The managed state follows the target
   exponentially:

       managed_n = (1 - ALPHA) * managed_{n-1} + ALPHA * target_n

   ALPHA = 0.35 per round. Rationale, from the measured numbers: the
   EMA's noise reduction factor is sqrt(ALPHA / (2 - ALPHA)) ≈ 0.46,
   so a raw round-to-round noise of ~18 ms disagreement publishes at
   ~8 ms; and the time constant 1/ALPHA ≈ 2.9 rounds means a real
   change in the fleet's behaviour is mostly reflected within about
   three rounds (three days at the daily cadence) while a single
   gust of WiFi weather moves the published timescale by at most
   ALPHA * GAMMA = 0.175 of its size. The same ALPHA and GAMMA apply
   to the drift state, so the managed drift is the fleet's rate,
   smoothed and anchored the same way.

Per-round correction bound: the steering correction the blender asks
for in a round — the amount the managed timescale should move toward
network time — is

    correction_n = -clamp(managed_offset_n, -BOUND_MS, +BOUND_MS)

with BOUND_MS = 10 ms. Ten milliseconds slewed over one Exocortical
Epoch quantum (420 s) is a ~24 ppm slew, far inside any daemon's slew
cap, and small enough that no round can yank the fleet: corrections
arrive as bounded quanta, per the design's framing. The tracking
error published each round is the managed offset itself — the managed
timescale's distance from network time, stated, never hidden.

State is atoms: blend.txt is append-only, one atom per consensus
round, carrying the state in and the state out, so the managed
timescale is derived from the register like every other QTS
designation. The first formed round seeds the state at its target
(there is no prior state to smooth against) and is marked
state=seeded.

This script changes nothing on any node; it computes and logs.

Usage: python3 blend.py [--consensus-log consensus.txt]
                        [--blend-log blend.txt] [--no-log]
"""
import argparse
import re
import sys
import time
from datetime import datetime, timezone

ALPHA = 0.35        # EMA rate per round (see module docstring)
GAMMA = 0.5         # weight on the fleet consensus in each round's
                    # target; (1 - GAMMA) weighs network time (zero)
BOUND_MS = 10.0     # per-round steering correction bound, ms
EE_ANCHOR = 1795327200
EE_QUANTUM_S = 420


def ee_of(unix_ts):
    if unix_ts < EE_ANCHOR:
        return 0
    return int((unix_ts - EE_ANCHOR) // EE_QUANTUM_S) + 1


def fmt(v, nd=3):
    return "none" if v is None else f"{v:.{nd}f}"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def parse_consensus_rounds(path):
    """Formed consensus atoms, in file order."""
    rounds = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("consensus ") and "formed=yes" in line:
                    atom = dict(kv.split("=", 1)
                                for kv in line.split()[1:] if "=" in kv)
                    atom["_stamp"] = line.split()[1]
                    rounds.append(atom)
    except FileNotFoundError:
        pass
    return rounds


def blended_censuses(path):
    """Census stamps already blended, from blend.txt."""
    done = set()
    try:
        with open(path) as f:
            for line in f:
                m = re.search(r"census=(\S+)", line)
                if m and line.startswith("blend "):
                    done.add(m.group(1))
    except FileNotFoundError:
        pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--consensus-log", default="consensus.txt")
    ap.add_argument("--blend-log", default="blend.txt")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    rounds = parse_consensus_rounds(args.consensus_log)
    if not rounds:
        print("no formed consensus rounds on record; nothing to blend",
              file=sys.stderr)
        return 1
    done = blended_censuses(args.blend_log)

    # Replay all rounds to derive current state (state is derived from
    # the register, never stored out of band).
    state = None  # (managed_offset_ms, managed_drift_ppm)
    new_atoms = []
    for r in rounds:
        raw_off = float(r["offset_ms"])
        raw_ppm = float(r["drift_ppm"])
        target_off = GAMMA * raw_off
        target_ppm = GAMMA * raw_ppm
        if state is None:
            m_off, m_ppm = target_off, target_ppm
            seeded = True
        else:
            m_off = (1 - ALPHA) * state[0] + ALPHA * target_off
            m_ppm = (1 - ALPHA) * state[1] + ALPHA * target_ppm
            seeded = False
        state = (m_off, m_ppm)
        correction = -clamp(m_off, -BOUND_MS, BOUND_MS)
        if r["census"] not in done:
            now = int(time.time())
            stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            new_atoms.append(
                f"blend {stamp} ee={ee_of(now)} census={r['census']} "
                f"alpha={ALPHA:.3f} gamma={GAMMA:.3f} "
                f"raw_offset_ms={fmt(raw_off)} "
                f"raw_drift_ppm={fmt(raw_ppm)} "
                f"managed_offset_ms={fmt(m_off)} "
                f"managed_drift_ppm={fmt(m_ppm)} "
                f"tracking_error_ms={fmt(m_off)} "
                f"correction_ms={fmt(correction)} "
                f"bound_ms={BOUND_MS:.1f} "
                f"state={'seeded' if seeded else 'running'} "
                "rule=ema-blend-to-network")

    m_off, m_ppm = state
    last = rounds[-1]
    print("MANAGED CONSENSUS (EMA blender)")
    print(f"  rounds blended: {len(rounds)} "
          f"(latest census {last['census']})")
    print(f"  alpha={ALPHA} gamma={GAMMA} bound={BOUND_MS:.0f} ms/round")
    print(f"  raw consensus:      offset {fmt(float(last['offset_ms']))} "
          f"ms, drift {fmt(float(last['drift_ppm']))} ppm")
    print(f"  managed consensus:  offset {fmt(m_off)} ms, "
          f"drift {fmt(m_ppm)} ppm "
          f"({fmt(m_ppm * 86.4, 1)} ms/day)")
    print(f"  tracking error vs network time: {fmt(m_off)} ms "
          "(the managed offset, stated)")
    print(f"  this round's steering correction: "
          f"{fmt(-clamp(m_off, -BOUND_MS, BOUND_MS))} ms "
          "(bounded, slew-only)")

    if new_atoms and not args.no_log:
        with open(args.blend_log, "a") as f:
            for a in new_atoms:
                f.write(a + "\n")
        print(f"  appended {len(new_atoms)} blend atom(s) to "
              f"{args.blend_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
