#!/usr/bin/env python3
"""Quantum Time Sync — subsets.txt refresh path (re-derivation).

subsets.txt is data, re-derivable at any census (TRIANGULATION.md);
it must never be a silently frozen constant. This tool re-derives it:

  SELECTION — over the last N non-superseded census blocks of a
  register, each node's durable traits are its MEDIAN score, |ppm|,
  and jitter across the rounds where it was eligible (the same trait
  evidence the EE 98 derivation used: pre-apply rounds against
  independent upstreams are the purest form, but any register's
  recent rounds are what a refresh has). Documented exclusions:
  aletheia-core (the hub carries coordinator load and the RH Lean
  build; its frequency readings swing with load), quacked-hill (a
  resident's node is never given serving load without Will's word),
  and any node not eligible in the latest block. The three lowest
  median scores win; the proposed rotation order is by median |ppm|
  (truest crystal first), matching triangulate.py's SURVEYOR_ORDER
  convention.

  ASSIGNMENT — each node to the surveyor it hears best: ICMP RTT
  from each candidate surveyor to every fleet node (min of 3 pings,
  the house min-delay discipline), measured by running ping ON the
  candidate over ssh — so this half needs a host whose ssh key the
  fleet accepts (the collector VM; the hub's key is refused by its
  peers). Balance cap 7 per subset including the surveyor; overflow
  moves at the smallest min-RTT penalty. Without --measure-links (or
  if ssh fails), the current membership is carried over — and if the
  surveyor SET changed, that is refused: new surveyors need a real
  link table, not a guess.

  HELD flags are carried from the current subsets.txt untouched:
  the 100 ms source-switch guard is an apply state, not a derivation.

The tool writes a PROPOSED file (default subsets.proposed.txt) and
prints a diff against the current subsets.txt. It never overwrites
subsets.txt. Applying a changed partition is a deliberate act: the
followers' timesyncd pointing, the surveyors' chrony peering, and
triangulate.py's SURVEYOR_ORDER must move with it.

Usage: python3 derive_subsets.py [--register register.txt]
           [--fleet fleet.txt] [--current subsets.txt]
           [--out subsets.proposed.txt] [--rounds 5]
           [--measure-links]
"""
import argparse
import math
import subprocess
import sys

from consensus import parse_register, fnum
from triangulate import parse_subsets

EXCLUDED_SURVEYORS = {
    "aletheia-core": "hub coordinator load (documented exclusion)",
    "quacked-hill": "resident's node (documented exclusion)",
}
SUBSET_NAMES = ["nk", "ep", "of"]   # stable labels; re-named only by hand
CAP = 7                             # nodes per subset incl. the surveyor
SSH_KEY = "/home/hatch/.ssh/id_ed25519"


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def traits(register_path, rounds):
    blocks = [b for b in parse_register(register_path)
              if not b["superseded"]][-rounds:]
    if not blocks:
        return None, {}
    latest_eligible = {n for n, a in blocks[-1]["nodes"].items()
                       if a.get("eligible") == "yes"}
    per = {}
    for b in blocks:
        for name, atom in b["nodes"].items():
            if atom.get("eligible") != "yes":
                continue
            d = per.setdefault(name, {"score": [], "ppm": [], "jit": []})
            for key, field in (("score", "score_ms"), ("ppm", "ppm"),
                               ("jit", "jitter_ms")):
                v = fnum(atom, field)
                if v is not None:
                    d[key].append(v)
    out = {}
    for name, d in per.items():
        if d["score"] and d["ppm"] and d["jit"]:
            out[name] = {"score": median(d["score"]),
                         "ppm": median(d["ppm"]),
                         "jit": median(d["jit"]),
                         "rounds": len(d["score"]),
                         "latest_eligible": name in latest_eligible}
    return blocks[-1], out


def measure_links(candidates, fleet):
    """{candidate: {node: min_rtt_ms}} via ping run on the candidate.
    Returns None if ssh to any candidate fails."""
    table = {}
    for cand in candidates:
        cip = fleet[cand]
        row = {}
        for name, ip in sorted(fleet.items()):
            if name == cand:
                row[name] = 0.0
                continue
            cmd = ["ssh", "-i", SSH_KEY, "-o", "IdentitiesOnly=yes",
                   "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
                   "-o", "ProxyCommand=nc -X connect -x "
                     "hatch-egress-proxy:3130 %h %p",
                   f"wbic16@{cip}", "ping", "-c", "3", "-q", "-W", "2", ip]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=30)
            except subprocess.TimeoutExpired:
                return None
            got = None
            for ln in r.stdout.splitlines():
                if "min/avg/max" in ln:
                    got = float(ln.split("=")[1].split("/")[0])
            if got is None:
                return None
            row[name] = got
        table[cand] = row
    return table


def assign(surveyors, fleet, links, held):
    """surveyors: ordered list of nodes. Returns {subset: (surveyor,
    [(node, held)])} by the documented rule."""
    label = {s: SUBSET_NAMES[i] for i, s in enumerate(surveyors)}
    members = {s: [] for s in surveyors}
    nodes = [n for n in fleet if n not in surveyors]
    # Best-first assignment with cap; overflow at smallest penalty.
    prefs = {}
    for n in nodes:
        prefs[n] = sorted(surveyors, key=lambda s: links[s][n])
    for n in sorted(nodes, key=lambda n: links[prefs[n][0]][n]):
        for s in prefs[n]:
            if len(members[s]) + 1 < CAP:   # +1 = the surveyor itself
                members[s].append(n)
                break
        else:
            return None
    return {label[s]: (s, [(n, held.get(n, False))
                           for n in members[s]]) for s in surveyors}


def render(partition, fleet, stamp_note):
    lines = [stamp_note]
    for subset in sorted(partition):
        s, mem = partition[subset]
        lines.append(f"surveyor {subset} {s} {fleet[s]}")
        for n, h in mem:
            lines.append(f"member {subset} {n} {fleet[n]} "
                         f"held={'yes' if h else 'no'}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--fleet", default="fleet.txt")
    ap.add_argument("--current", default="subsets.txt")
    ap.add_argument("--out", default="subsets.proposed.txt")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--measure-links", action="store_true")
    args = ap.parse_args()

    fleet = {}
    with open(args.fleet) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                p = line.split()
                fleet[p[0]] = p[1]

    latest, tr = traits(args.register, args.rounds)
    if latest is None:
        print("no usable census blocks", file=sys.stderr)
        return 1
    print(f"derivation evidence: last {args.rounds} block(s), latest "
          f"census {latest['stamp']} (EE {latest['ee']})")
    ranked = sorted(
        (n for n, d in tr.items()
         if d["latest_eligible"] and n not in EXCLUDED_SURVEYORS),
        key=lambda n: tr[n]["score"])
    for n in ranked:
        d = tr[n]
        print(f"  candidate {n:<18} median score={d['score']:.3f} ms  "
              f"|ppm|={abs(d['ppm']):.3f}  jitter={d['jit']:.3f} ms  "
              f"({d['rounds']} rounds)")
    for n, why in EXCLUDED_SURVEYORS.items():
        if n in tr:
            print(f"  excluded  {n:<18} {why}")
    if len(ranked) < 3:
        print("fewer than 3 candidates — cannot derive", file=sys.stderr)
        return 1
    chosen = sorted(ranked[:3], key=lambda n: abs(tr[n]["ppm"]))
    print(f"proposed surveyors (truest crystal first): "
          f"{', '.join(chosen)}")

    cur_surveyors, cur_members = parse_subsets(args.current)
    cur_partition = {}
    held = {}
    for subset, (sname, sip) in cur_surveyors.items():
        mem = cur_members.get(subset, [])
        cur_partition[subset] = (sname, [(n, h) for (n, ip, h) in mem])
        for (n, ip, h) in mem:
            held[n] = h
    cur_set = {s for (s, ip) in cur_surveyors.values()}

    if set(chosen) == cur_set:
        # Same surveyors: keep the subset labels of record and the
        # current membership unless links are re-measured.
        label_of = {s: sub for sub, (s, ip) in cur_surveyors.items()}
        if args.measure_links:
            links = measure_links(chosen, fleet)
            if links is None:
                print("link measurement failed (ssh/ping) — carrying "
                      "current membership", file=sys.stderr)
                partition = cur_partition
            else:
                partition = assign([cur_surveyors[s][0]
                                    for s in sorted(cur_surveyors)],
                                   fleet, links, held)
                if partition is None:
                    print("assignment failed under the cap",
                          file=sys.stderr)
                    return 1
        else:
            partition = cur_partition
    else:
        if not args.measure_links:
            print("surveyor SET changes under the new evidence — a "
                  "link round is required: re-run with "
                  "--measure-links from a host whose ssh key the "
                  "fleet accepts.", file=sys.stderr)
            return 2
        links = measure_links(chosen, fleet)
        if links is None:
            print("link measurement failed (ssh/ping)",
                  file=sys.stderr)
            return 1
        partition = assign(chosen, fleet, links, held)
        if partition is None:
            print("assignment failed under the cap", file=sys.stderr)
            return 1
        print("NOTE: surveyor set changed — triangulate.py's "
              "SURVEYOR_ORDER, the chrony peering, and the followers' "
              "timesyncd pointing must be re-applied with it.")

    note = (f"# PROPOSED subsets — derived by derive_subsets.py from "
            f"{args.register} (latest census {latest['stamp']}, "
            f"EE {latest['ee']}); NOT applied. See TRIANGULATION.md "
            f"for the rule and the apply discipline.")
    proposed = render(partition, fleet, note)
    with open(args.out, "w") as f:
        f.write("\n".join(proposed) + "\n")
    current = render(cur_partition, fleet, "# current")
    pset = {ln for ln in proposed[1:] if ln.strip()}
    cset = {ln for ln in current[1:] if ln.strip()}
    if pset == cset:
        print(f"no change: proposed partition == current "
              f"({args.current})")
    else:
        print(f"diff vs {args.current}:")
        for ln in sorted(cset - pset):
            print(f"  - {ln}")
        for ln in sorted(pset - cset):
            print(f"  + {ln}")
    print(f"proposed file written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
