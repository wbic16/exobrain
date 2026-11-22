#!/usr/bin/env python3
"""Quantum Time Sync — sync-apply path (DRY-RUN ONLY in this pass).

Reads the latest election (elections.txt) and the latest census
(register.txt) and prints, per node, exactly what WOULD change to
discipline that node to the designated master, plus the steering changes
that WOULD be made on the master itself.

Nothing is applied. There is no apply mode in this pass: fleet clock
reconfiguration waits for Will's word after the first designation.
Clocks are never stepped by QTS in any mode (DESIGN.md, SYNC section).

Usage: python3 apply_sync.py --dry-run
"""
import argparse
import re
import sys

STEP_GUARD_MS = 100.0  # QTS rule: no source-switch restart at/above this
                       # |offset|; the node slews down under its current
                       # source first, or Will approves it per-node.


def latest_block(path):
    blocks, cur = [], None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("census "):
                cur = {"stamp": line.split()[1], "nodes": {}}
                blocks.append(cur)
            elif line.startswith("node ") and cur is not None:
                atom = dict(kv.split("=", 1) for kv in line.split()[1:]
                            if "=" in kv)
                atom["node"] = line.split()[1]
                cur["nodes"][atom["node"]] = atom
            elif line == "end-census":
                cur = None
    return blocks[-1]


def latest_master(path):
    master = None
    with open(path) as f:
        for line in f:
            if line.startswith("election "):
                m = re.search(r"master=(\S+)", line)
                if m and m.group(1) != "none":
                    master = m.group(1)
    return master


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="the only mode in this pass")
    ap.add_argument("--register", default="register.txt")
    ap.add_argument("--elections", default="elections.txt")
    args = ap.parse_args()
    if not args.dry_run:
        print("apply mode is not built in this pass. Fleet clock changes "
              "wait for Will's word after the first designation.\n"
              "Run with --dry-run to see exactly what would change.")
        return 2

    block = latest_block(args.register)
    master = latest_master(args.elections)
    if master is None:
        print("no designated master on record; run elect.py first")
        return 1
    m_atom = block["nodes"][master]
    m_ip = m_atom["ip"]
    m_ppm = float(m_atom["ppm"])
    print(f"DRY RUN — no changes will be made.")
    print(f"Designated master: {master} ({m_ip}), census {block['stamp']}, "
          f"measured frequency {m_ppm:+.3f} ppm.")
    print(f"Free-run budget at that drift: predicted error reaches 5 ms "
          f"after {5.0 / (abs(m_ppm) / 1000.0):.0f} s "
          f"({5.0 / (abs(m_ppm) / 1000.0) / 60.0:.1f} min) — the master's "
          "network steering cadence is set well inside it.\n")

    for name in sorted(block["nodes"]):
        atom = block["nodes"][name]
        if atom.get("reachable") != "yes":
            print(f"[{name}] unreachable in latest census "
                  f"({atom.get('reason', 'no reason recorded')}) — "
                  "no change proposed.")
            continue
        engine = atom.get("engine", "none")
        off = atom.get("offset_ms", "none")
        if name == master:
            print(f"[{name}] MASTER — steering changes only:")
            print("  /etc/systemd/timesyncd.conf [Time] would gain:")
            print("    PollIntervalMinSec=32")
            print("    PollIntervalMaxSec=1024   (tightened from the "
                  "observed 2048 s max poll, so network checks land well")
            print("                             inside the predicted-drift "
                  "budget)")
            print("  A drift model atom file would be started at "
                  "~/qts/drift.txt on the master: one line per census "
                  "(stamp, ppm, offset), from which predicted offset = "
                  "ppm x elapsed is computed between network checks.")
            print("  Its upstream stays the network pool "
                  f"(currently {atom.get('server')}). No clock step; "
                  "timesyncd slews only.\n")
            continue
        print(f"[{name}] engine={engine}, current source "
              f"{atom.get('server')}, last offset {off} ms")
        if engine == "systemd-timesyncd":
            print("  /etc/systemd/timesyncd.conf would be backed up to "
                  "timesyncd.conf.qts-bak, then [Time] would read:")
            print(f"    NTP={m_ip}")
            print("    FallbackNTP=ntp.ubuntu.com "
                  "(today's upstream, kept so a dark master degrades to "
                  "current behaviour, never to free-run)")
            print("  Then: systemctl restart systemd-timesyncd "
                  "(one node at a time, never fleet-wide at once).")
            try:
                if abs(float(off)) >= STEP_GUARD_MS:
                    print(f"  GUARD: |offset| {off} ms >= "
                          f"{STEP_GUARD_MS:.0f} ms — this node would NOT "
                          "be switched yet; it slews down under its "
                          "current source first, or Will approves it "
                          "per-node.")
            except (TypeError, ValueError):
                print("  GUARD: offset unmeasured in latest census — "
                      "node would be re-censused before any switch.")
        elif engine == "chrony":
            print("  /etc/chrony/conf.d/qts.conf would be created with:")
            print(f"    server {m_ip} iburst prefer")
            print("  Existing upstream servers stay as non-preferred "
                  "fallback. Any existing makestep directive with an "
                  "unbounded limit would be flagged to Will, not edited "
                  "silently. Then: systemctl restart chrony (chrony "
                  "slews; QTS never issues a step).")
        else:
            print("  No recognised time engine — no change proposed; "
                  "flagged for a human look.")
        print()
    print("End of dry run. Nothing was changed on any node.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
