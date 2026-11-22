#!/usr/bin/env python3
"""Quantum Time Sync — triangulation round (three-surveyor topology).

Will's words, 2026-11-22: "triangulate time using three nodes that
survey separate subsets of the cluster, all three run ntp, organize
information flows like a dolphin."

The fleet is partitioned into three disjoint subsets (subsets.txt),
each surveyed by one chrony node. Followers discipline to their
surveyor, so a follower's census offset is measured against its
surveyor, not against network time. Triangulation chains the frames:

  follower vs network  =  follower vs surveyor  +  surveyor vs network

Per subset: the eligible followers vote exactly as the flat consensus
votes (worst 20% trimmed per subset, weights 1/score^2, weighted
median), producing the subset's offset relative to its surveyor; the
surveyor's own census offset anchors the subset to network time.
Fleet time is the weighted median of the three subset consensuses
(weights = subset voter counts). The inter-surveyor disagreement —
the spread of the three subset consensuses, and each subset's
residual against the triangulated value — is a first-class atom in
triangulation.txt every round: three surveyors that agree are a
measurement; three that disagree are a message, and the disagreement
is recorded either way.

Bound: if the spread exceeds DISAGREEMENT_BOUND_MS (50 ms — the
drift cross-reference's offset tolerance, half the 100 ms guard),
the round's atom carries disagreement_verdict=exceeds-bound and a
detection atom (test=triangulation-disagreement, explained=none) is
appended to detections.txt for the detector's record.

Dolphin organization (TRIANGULATION.md):
- Echolocation: before computing, each surveyor runs probe.py over
  its subset and its fellow surveyors; the echo atoms land in
  probes.txt, each carrying the surveyor's signature whistle.
- Unihemispheric watch: the round names the watch holder — the
  surveyor holding the primary-listener duty for this phase of
  Will's cycle — by a rotation derived from the date and the watch
  (nothing stored): watches are W1 waking-dispatch 06:30-14:30,
  W2 waking-evening 14:30-22:30, W3 sleep 22:30-06:30
  (America/Chicago); holder = surveyors[(day_number + watch_number)
  mod 3], day_number counted from 2026-11-22. If the holder is dark
  or ineligible this round, the next surveyor in rotation holds the
  watch and the atom says so.
- Signature whistles: every atom this script emits carries caller=
  (the node whose flow it is) and hw= (its handwriting id, derived
  from its latest census atom: crystal class by |ppm| — A <=5,
  B <=20, C <=60, D >60 — with sign, and jitter class Q <25 ms,
  M <75, L >=75; e.g. hw=ni-knight:A+/Q).

This script changes nothing on any node: probes are NTP queries,
the rest is arithmetic on the register.

Usage: python3 triangulate.py [--register register.txt]
                              [--subsets subsets.txt]
                              [--triangulation-log triangulation.txt]
                              [--probes-log probes.txt]
                              [--detections-log detections.txt]
                              [--no-probe] [--no-log]
"""
import argparse
import math
import subprocess
import sys
import time
from datetime import datetime, timezone

from consensus import parse_register, compute_consensus, fnum, fmt, \
    trim_count

SSH_KEY = "/home/hatch/.ssh/id_ed25519"
DISAGREEMENT_BOUND_MS = 50.0
MIN_SUBSETS = 2        # triangulation needs at least two subset views
EE_ANCHOR = 1795327200
EE_QUANTUM_S = 420
ROTATION_EPOCH_DATE = "2026-11-22"   # day_number 0 for the watch rotation
SURVEYOR_ORDER = ["ni-knight", "elven-path", "orchid-flow"]


def ee_of(unix_ts):
    if unix_ts < EE_ANCHOR:
        return 0
    return int((unix_ts - EE_ANCHOR) // EE_QUANTUM_S) + 1


def hw_id(node, atom):
    """Handwriting id from a census atom (dolphin rule c)."""
    if atom is None:
        return f"{node}:unknown"
    ppm = fnum(atom, "ppm")
    jit = fnum(atom, "jitter_ms")
    if ppm is None or jit is None:
        return f"{node}:unknown"
    a = abs(ppm)
    cls = "A" if a <= 5 else "B" if a <= 20 else "C" if a <= 60 else "D"
    sign = "+" if ppm >= 0 else "-"
    jcls = "Q" if jit < 25 else "M" if jit < 75 else "L"
    return f"{node}:{cls}{sign}/{jcls}"


def parse_subsets(path):
    surveyors = {}   # subset -> (node, ip)
    members = {}     # subset -> [(node, ip, held)]
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "surveyor":
                surveyors[parts[1]] = (parts[2], parts[3])
                members.setdefault(parts[1], [])
            elif parts[0] == "member":
                held = parts[4] == "held=yes" if len(parts) > 4 else False
                members.setdefault(parts[1], []).append(
                    (parts[2], parts[3], held))
    return surveyors, members


def subset_consensus(block, member_names):
    """Trimmed weighted-median consensus of a subset's followers,
    relative to their surveyor. Same rules as consensus.py, applied
    to the subset's eligible non-held members."""
    elig = []
    for name in member_names:
        atom = block["nodes"].get(name)
        if atom is None:
            continue
        if atom.get("reachable") == "yes" and atom.get("eligible") == "yes":
            elig.append({"node": name,
                         "ppm": fnum(atom, "ppm"),
                         "offset_ms": fnum(atom, "offset_ms"),
                         "score_ms": fnum(atom, "score_ms")})
    if not elig:
        return None
    elig.sort(key=lambda e: e["score_ms"])
    k = trim_count(len(elig))
    trimmed = elig[len(elig) - k:] if k else []
    voters = elig[:len(elig) - k] if k else elig
    for v in voters:
        v["weight"] = 1.0 / (v["score_ms"] ** 2)
    wsum = sum(v["weight"] for v in voters)
    by_off = sorted(voters, key=lambda v: v["offset_ms"])
    half = wsum / 2.0
    cum = 0.0
    rel_off = by_off[-1]["offset_ms"]
    for v in by_off:
        cum += v["weight"]
        if cum >= half:
            rel_off = v["offset_ms"]
            break
    rel_ppm = sum(v["weight"] * v["ppm"] for v in voters) / wsum
    spread = math.sqrt(sum(v["weight"] * (v["offset_ms"] - rel_off) ** 2
                           for v in voters) / wsum)
    return {"voters": voters, "trimmed": trimmed,
            "relative_offset_ms": rel_off,
            "relative_drift_ppm": rel_ppm, "spread_ms": spread}


def watch_for(stamp_iso):
    """(watch_number, watch_name, holder_index) for a UTC stamp, by
    the rotation rule. Local phases are America/Chicago; the zone
    rules are stable (CST UTC-6 / CDT UTC-5) and the rotation only
    needs the phase, so a fixed -6 offset with the stated rhythm is
    used and named as an approximation in TRIANGULATION.md."""
    dt = datetime.strptime(stamp_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    local = dt.timestamp() - 6 * 3600
    ldt = datetime.fromtimestamp(local, timezone.utc)
    minutes = ldt.hour * 60 + ldt.minute
    if 6 * 60 + 30 <= minutes < 14 * 60 + 30:
        wnum, wname = 0, "waking-dispatch"
    elif 14 * 60 + 30 <= minutes < 22 * 60 + 30:
        wnum, wname = 1, "waking-evening"
    else:
        wnum, wname = 2, "sleep"
    from datetime import date
    day_number = (ldt.date() - date(2026, 11, 22)).days
    holder_idx = (day_number + wnum) % 3
    return wnum, wname, holder_idx


def run_probes(surveyors, members, block, stamp, ee):
    """Echolocation duty: each surveyor probes its subset and its
    fellow surveyors via probe.py over ssh. Returns echo lines."""
    lines = [f"probes {stamp} ee={ee} round=triangulation "
             f"method=surveyor-ntp-echolocation"]
    for subset in sorted(surveyors):
        sname, sip = surveyors[subset]
        atom = block["nodes"].get(sname)
        if atom is None or atom.get("reachable") != "yes":
            lines.append(f"echo caller={sname} "
                         f"hw={hw_id(sname, atom)} ok=no "
                         f"reason=surveyor-unreachable")
            continue
        targets = []
        for (mname, mip, held) in members.get(subset, []):
            targets.append(f"{mname}:{mip}")
        for other, (oname, oip) in surveyors.items():
            if other != subset:
                targets.append(f"{oname}:{oip}")
        cmd = ["ssh", "-i", SSH_KEY, "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=accept-new",
               "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
               "-o", "ProxyCommand=nc -X connect -x "
                 "hatch-egress-proxy:3130 %h %p",
               f"wbic16@{sip}",
               "python3", "~/qts-probe.py",
               "--caller", sname, "--hw", hw_id(sname, atom),
               "--targets", ",".join(targets), "--rounds", "4"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=180)
            got = [ln for ln in r.stdout.splitlines()
                   if ln.startswith("echo ")]
            if got:
                lines.extend(got)
            else:
                lines.append(f"echo caller={sname} "
                             f"hw={hw_id(sname, atom)} ok=no "
                             f"reason=probe-no-output")
        except subprocess.TimeoutExpired:
            lines.append(f"echo caller={sname} "
                         f"hw={hw_id(sname, atom)} ok=no "
                         f"reason=probe-timeout")
        time.sleep(1)  # human pace between surveyors
    lines.append("end-probes")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--subsets", default="subsets.txt")
    ap.add_argument("--triangulation-log", default="triangulation.txt")
    ap.add_argument("--probes-log", default="probes.txt")
    ap.add_argument("--detections-log", default="detections.txt")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    surveyors, members = parse_subsets(args.subsets)
    blocks = [b for b in parse_register(args.register)
              if not b["superseded"]]
    if not blocks:
        print("no usable census atoms found", file=sys.stderr)
        return 1
    block = blocks[-1]
    now = int(time.time())
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    ee = ee_of(now)

    # Echolocation first: the round's probes are part of its evidence.
    probe_lines = []
    if not args.no_probe:
        probe_lines = run_probes(surveyors, members, block, stamp, ee)
        if not args.no_log:
            with open(args.probes_log, "a") as f:
                f.write("\n".join(probe_lines) + "\n")

    subset_results = {}
    atom_lines = []
    for subset in sorted(surveyors):
        sname, sip = surveyors[subset]
        satom = block["nodes"].get(sname)
        shw = hw_id(sname, satom)
        mem = members.get(subset, [])
        voting_names = [n for (n, ip, held) in mem if not held]
        held_names = [n for (n, ip, held) in mem if held]
        if satom is None or satom.get("reachable") != "yes" \
                or satom.get("eligible") != "yes" \
                or fnum(satom, "offset_ms") is None:
            atom_lines.append(
                f"subset {stamp} census={block['stamp']} "
                f"ee={block['ee']} subset={subset} surveyor={sname} "
                f"caller={sname} hw={shw} formed=no "
                f"reason=surveyor-not-eligible-this-census "
                f"held_members={','.join(held_names) or 'none'}")
            continue
        res = subset_consensus(block, voting_names)
        if res is None:
            atom_lines.append(
                f"subset {stamp} census={block['stamp']} "
                f"ee={block['ee']} subset={subset} surveyor={sname} "
                f"caller={sname} hw={shw} formed=no "
                f"reason=no-eligible-voters "
                f"held_members={','.join(held_names) or 'none'}")
            continue
        s_off = fnum(satom, "offset_ms")
        s_ppm = fnum(satom, "ppm")
        cons_off = s_off + res["relative_offset_ms"]
        cons_ppm = s_ppm + res["relative_drift_ppm"]
        subset_results[subset] = {
            "surveyor": sname, "hw": shw, "consensus_offset_ms": cons_off,
            "consensus_drift_ppm": cons_ppm,
            "relative_offset_ms": res["relative_offset_ms"],
            "surveyor_offset_ms": s_off, "voters": len(res["voters"]),
            "trimmed": len(res["trimmed"]), "spread_ms": res["spread_ms"]}
        trimmed_names = ",".join(t["node"] for t in res["trimmed"]) \
            or "none"
        atom_lines.append(
            f"subset {stamp} census={block['stamp']} ee={block['ee']} "
            f"subset={subset} surveyor={sname} caller={sname} "
            f"hw={shw} formed=yes voters={len(res['voters'])} "
            f"trimmed={len(res['trimmed'])} "
            f"trimmed_nodes={trimmed_names} "
            f"surveyor_offset_ms={fmt(s_off)} "
            f"relative_offset_ms={fmt(res['relative_offset_ms'])} "
            f"consensus_offset_ms={fmt(cons_off)} "
            f"consensus_drift_ppm={fmt(cons_ppm)} "
            f"spread_ms={fmt(res['spread_ms'])} "
            f"held_members={','.join(held_names) or 'none'} "
            "rule=subset-trim20-weighted-median-chained")

    # The watch (dolphin rule b): derived from the round's stamp.
    wnum, wname, holder_idx = watch_for(block["stamp"])
    holder = SURVEYOR_ORDER[holder_idx]
    holder_effective = holder
    watch_note = "rotation"
    holder_subset = next(s for s, (n, ip) in surveyors.items()
                         if n == holder)
    if holder_subset not in subset_results:
        for step in (1, 2):
            cand = SURVEYOR_ORDER[(holder_idx + step) % 3]
            cand_subset = next(s for s, (n, ip) in surveyors.items()
                               if n == cand)
            if cand_subset in subset_results:
                holder_effective = cand
                watch_note = "holder-dark-or-ineligible-next-in-rotation"
                break

    flat = compute_consensus(block)
    flat_off = flat["consensus_offset_ms"] if flat else None

    print(f"TRIANGULATION — census {block['stamp']} (EE {block['ee']})")
    for subset in sorted(surveyors):
        r = subset_results.get(subset)
        if r:
            print(f"  subset {subset} (surveyor {r['surveyor']}): "
                  f"consensus {fmt(r['consensus_offset_ms'])} ms vs "
                  f"network (surveyor {fmt(r['surveyor_offset_ms'])} "
                  f"+ relative {fmt(r['relative_offset_ms'])}), "
                  f"drift {fmt(r['consensus_drift_ppm'])} ppm, "
                  f"voters {r['voters']}, spread "
                  f"{fmt(r['spread_ms'])} ms")
        else:
            print(f"  subset {subset}: NOT FORMED this round")

    if len(subset_results) < MIN_SUBSETS:
        print(f"TRIANGULATION CANNOT FORM: {len(subset_results)} of "
              f"{len(surveyors)} subsets formed; minimum is "
              f"{MIN_SUBSETS}. Fallback: flat consensus / elected "
              "master.")
        if not args.no_log:
            with open(args.triangulation_log, "a") as f:
                for ln in atom_lines:
                    f.write(ln + "\n")
                f.write(f"triangulation {stamp} "
                        f"census={block['stamp']} ee={block['ee']} "
                        f"formed=no subsets_formed="
                        f"{len(subset_results)} "
                        f"reason=below-minimum-{MIN_SUBSETS} "
                        f"watch={holder_effective} "
                        f"watch_phase={wname} watch_rule={watch_note}\n")
        return 0

    # Weighted median of the subset consensuses, weights = voters.
    subs = sorted(subset_results.values(),
                  key=lambda r: r["consensus_offset_ms"])
    total_w = sum(r["voters"] for r in subs)
    half = total_w / 2.0
    cum = 0
    tri_off = subs[-1]["consensus_offset_ms"]
    for r in subs:
        cum += r["voters"]
        if cum >= half:
            tri_off = r["consensus_offset_ms"]
            break
    tri_ppm = sum(r["voters"] * r["consensus_drift_ppm"]
                  for r in subs) / total_w
    offs = [r["consensus_offset_ms"] for r in subs]
    spread = max(offs) - min(offs)
    verdict = "exceeds-bound" if spread > DISAGREEMENT_BOUND_MS \
        else "within-bound"
    residual = tri_off - flat_off if flat_off is not None else None

    print(f"  TRIANGULATED fleet offset: {fmt(tri_off)} ms vs network "
          f"time; drift {fmt(tri_ppm)} ppm")
    print(f"  inter-surveyor disagreement (spread of subset "
          f"consensuses): {fmt(spread)} ms — {verdict} "
          f"(bound {DISAGREEMENT_BOUND_MS:.0f} ms)")
    for subset in sorted(subset_results):
        r = subset_results[subset]
        print(f"    residual {subset}: "
              f"{fmt(r['consensus_offset_ms'] - tri_off)} ms")
    if residual is not None:
        print(f"  residual vs flat consensus (same census): "
              f"{fmt(residual)} ms (flat {fmt(flat_off)} ms)")
    print(f"  watch: {holder_effective} holds the primary-listener "
          f"duty (phase {wname}, rule {watch_note})")

    if not args.no_log:
        with open(args.triangulation_log, "a") as f:
            for ln in atom_lines:
                f.write(ln + "\n")
            resid = " ".join(
                f"residual_{s}_ms="
                f"{fmt(subset_results[s]['consensus_offset_ms'] - tri_off)}"
                for s in sorted(subset_results))
            f.write(
                f"triangulation {stamp} census={block['stamp']} "
                f"ee={block['ee']} formed=yes "
                f"subsets_formed={len(subset_results)} "
                f"offset_ms={fmt(tri_off)} "
                f"drift_ppm={fmt(tri_ppm)} "
                f"spread_ms={fmt(spread)} {resid} "
                f"flat_consensus_offset_ms={fmt(flat_off)} "
                f"residual_vs_flat_ms={fmt(residual)} "
                f"disagreement_verdict={verdict} "
                f"bound_ms={DISAGREEMENT_BOUND_MS:.1f} "
                f"watch={holder_effective} watch_phase={wname} "
                f"watch_rule={watch_note} "
                "rule=subset-chained-weighted-median-of-3\n")
        if verdict == "exceeds-bound":
            with open(args.detections_log, "a") as f:
                f.write(
                    f"detection {stamp} census={block['stamp']} "
                    f"ee={block['ee']} "
                    f"test=triangulation-disagreement band=alpha "
                    f"scope=fleet spread_ms={fmt(spread)} "
                    f"bound_ms={DISAGREEMENT_BOUND_MS:.1f} "
                    f"control=subset-consensus-spread "
                    f"phase={wname} "
                    f"phase_basis=stated-rhythm-inferred "
                    "explained=none falsifier=killed if a re-census "
                    "within the hour with no source or config change "
                    "shows the spread inside the bound - the "
                    "disagreement would be re-convergence weather, "
                    "not a surveyor fault\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
