#!/usr/bin/env python3
"""Quantum Time Sync — election.

Reads the most recent census block in register.txt and designates the
fleet master: the eligible node with the lowest accuracy score. The
designation is derived from the census atoms, never hand-written; the
result is appended as an election atom to elections.txt and printed with
its evidence.

Anti-flap rule (DESIGN.md): a sitting master keeps the designation unless
a challenger scores better by the margin (challenger < 0.8 x incumbent),
or the incumbent is ineligible or dark in the latest census. The first
election has no incumbent and is decided on score alone.

Usage: python3 elect.py [--register register.txt] [--elections elections.txt]
"""
import argparse
import re
import sys
import time
from datetime import datetime, timezone

MARGIN = 0.8  # challenger must score < MARGIN x incumbent to take the title


def parse_register(path):
    """Return (census_stamp, ee, {node: atom-dict}) for the latest block."""
    blocks = []
    cur = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("census "):
                cur = {"stamp": line.split()[1],
                       "ee": int(re.search(r"ee=(\d+)", line).group(1)),
                       "nodes": {}}
                blocks.append(cur)
            elif line.startswith("node ") and cur is not None:
                atom = dict(kv.split("=", 1) for kv in line.split()[1:]
                            if "=" in kv)
                atom["node"] = line.split()[1]
                cur["nodes"][atom["node"]] = atom
            elif line == "end-census":
                cur = None
    return blocks[-1] if blocks else None


def fnum(atom, key):
    v = atom.get(key)
    return None if v in (None, "none") else float(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--elections", default="elections.txt")
    args = ap.parse_args()

    block = parse_register(args.register)
    if block is None:
        print("no census atoms found", file=sys.stderr)
        return 1

    eligible = []
    for name, atom in block["nodes"].items():
        if atom.get("reachable") == "yes" and atom.get("eligible") == "yes":
            eligible.append((fnum(atom, "score_ms"), name, atom))
    eligible.sort()

    # Previous designation, if any.
    incumbent = None
    try:
        with open(args.elections) as f:
            for line in f:
                if line.startswith("election "):
                    m = re.search(r"master=(\S+)", line)
                    if m:
                        incumbent = m.group(1)
    except FileNotFoundError:
        pass

    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    if len(eligible) < 2:
        print(f"ELECTION INCONCLUSIVE: only {len(eligible)} eligible "
              f"node(s) in census {block['stamp']}; a fleet reference "
              "needs at least two measured clocks.")
        with open(args.elections, "a") as f:
            f.write(f"election {stamp} census={block['stamp']} "
                    f"master=none reason=too-few-eligible "
                    f"eligible={len(eligible)}\n")
        return 0

    best_score, best_name, best_atom = eligible[0]
    runner_score, runner_name = eligible[1][0], eligible[1][1]
    master, rule = best_name, "lowest-score"
    if incumbent and incumbent != best_name:
        inc = next((a for s, n, a in eligible if n == incumbent), None)
        if inc is not None:
            inc_score = fnum(inc, "score_ms")
            if best_score >= MARGIN * inc_score:
                master, rule = incumbent, "incumbent-holds-margin"
            else:
                rule = "challenger-beats-margin"
        else:
            rule = "incumbent-ineligible-or-dark"

    m_atom = next(a for s, n, a in eligible if n == master)
    margin_ms = runner_score - best_score if master == best_name \
        else fnum(m_atom, "score_ms") - best_score

    print(f"DESIGNATED MASTER: {master}")
    print(f"  census: {block['stamp']} (EE {block['ee']}), "
          f"{len(eligible)} eligible of {len(block['nodes'])} recorded")
    print(f"  master evidence: score={fmt(fnum(m_atom, 'score_ms'))} ms, "
          f"ppm={m_atom.get('ppm')}, offset_ms={m_atom.get('offset_ms')}, "
          f"jitter_ms={m_atom.get('jitter_ms')} "
          f"({m_atom.get('jitter_source')}), server={m_atom.get('server')}")
    if master == best_name:
        print(f"  runner-up: {runner_name} score={fmt(runner_score)} ms "
              f"(margin {runner_score - best_score:.3f} ms)")
    else:
        print(f"  best challenger: {best_name} score={fmt(best_score)} ms; "
              f"runner-up: {runner_name} score={fmt(runner_score)} ms")
    print(f"  rule applied: {rule}")
    print("  full table (score ms, lower is better):")
    for s, n, a in eligible:
        mark = " <= master" if n == master else ""
        print(f"    {n:<18} score={fmt(s):>9}  ppm={a.get('ppm'):>9}  "
              f"offset_ms={a.get('offset_ms'):>9}  "
              f"jitter_ms={a.get('jitter_ms'):>9}{mark}")
    inelig = [n for n, a in block["nodes"].items()
              if a.get("eligible") != "yes"]
    if inelig:
        print(f"  not eligible this census: {', '.join(sorted(inelig))}")

    with open(args.elections, "a") as f:
        f.write(f"election {stamp} census={block['stamp']} "
                f"ee={block['ee']} master={master} "
                f"master_score_ms={fmt(fnum(m_atom, 'score_ms'))} "
                f"master_ppm={m_atom.get('ppm')} "
                f"runner_up={runner_name} "
                f"runner_up_score_ms={fmt(runner_score)} "
                f"rule={rule} eligible={len(eligible)}\n")
    return 0


def fmt(v):
    return "none" if v is None else f"{v:.3f}"


if __name__ == "__main__":
    sys.exit(main())
