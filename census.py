#!/usr/bin/env python3
"""Quantum Time Sync — fleet clock census.

Walks the fleet roster (fleet.txt) over ssh, one node at a time, read-only,
and appends one atom per node to register.txt. An atom records what the
node's own time discipline daemon reports about its clock: frequency error
(ppm), offset, jitter, sync state, engine, and network source.

No clock is ever changed by this script. No config is read beyond what
timedatectl / chronyc print. Probes are sequential with a pause between
nodes (human pace).

Usage: python3 census.py [--fleet fleet.txt] [--register register.txt]
"""
import argparse
import re
import subprocess
import sys
import time

SSH_KEY = "/home/hatch/.ssh/id_ed25519"
EE_ANCHOR = 1795327200  # EE 1 begins 2026-11-22 00:00:00 CST (unix)
EE_QUANTUM_S = 420

REMOTE = r"""
hostname
echo ===TIMEDATECTL===
timedatectl 2>/dev/null
echo ===TIMESYNC===
timedatectl timesync-status 2>/dev/null
echo ===CHRONY===
chronyc tracking 2>/dev/null
echo ===CHRONY-SOURCES===
chronyc -n sources 2>/dev/null | head -6
echo ===SERVICES===
for s in chrony chronyd systemd-timesyncd; do printf '%s=' $s; systemctl is-active $s 2>/dev/null; done
echo ===END===
"""


def ee_of(unix_ts):
    if unix_ts < EE_ANCHOR:
        return 0
    return int((unix_ts - EE_ANCHOR) // EE_QUANTUM_S) + 1


def parse_unit_ms(value, unit):
    v = float(value)
    if unit == "ms":
        return v
    if unit in ("us", "µs"):
        return v / 1000.0
    if unit == "s":
        return v * 1000.0
    raise ValueError(unit)


def parse_probe(text):
    """Return a dict of measured fields from one node's probe output."""
    d = {"synced": None, "engine": None, "server": None, "ppm": None,
         "offset_ms": None, "jitter_ms": None, "jitter_source": None}
    sections = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"===([A-Z-]+)===", line.strip())
        if m:
            cur = m.group(1)
            sections[cur] = []
        elif cur:
            sections[cur].append(line)

    td = "\n".join(sections.get("TIMEDATECTL", []))
    m = re.search(r"System clock synchronized:\s*(\w+)", td)
    if m:
        d["synced"] = (m.group(1) == "yes")

    svc = "\n".join(sections.get("SERVICES", []))
    active = set(re.findall(r"(\S+)=active", svc))
    if "chrony" in active or "chronyd" in active:
        d["engine"] = "chrony"
    elif "systemd-timesyncd" in active:
        d["engine"] = "systemd-timesyncd"

    ts = "\n".join(sections.get("TIMESYNC", []))
    if ts.strip():
        m = re.search(r"Server:\s*(\S+)", ts)
        if m:
            d["server"] = m.group(1)
        m = re.search(r"Frequency:\s*([+-]?[\d.]+)\s*ppm", ts)
        if m:
            d["ppm"] = float(m.group(1))
        for field, key in (("Offset", "offset_ms"), ("Jitter", "jitter_ms")):
            m = re.search(field + r":\s*([+-]?[\d.]+)\s*(ms|us|µs|s)\b", ts)
            if m:
                d[key] = parse_unit_ms(m.group(1), m.group(2))
        if d["jitter_ms"] is not None:
            d["jitter_source"] = "timesyncd-jitter"

    ch = "\n".join(sections.get("CHRONY", []))
    if ch.strip():
        m = re.search(r"System time\s*:\s*([\d.]+)\s*seconds\s+(slow|fast)", ch)
        if m:
            off = float(m.group(1)) * 1000.0
            d["offset_ms"] = -off if m.group(2) == "slow" else off
        m = re.search(r"Frequency\s*:\s*([\d.]+)\s*ppm\s+(slow|fast)", ch)
        if m:
            ppm = float(m.group(1))
            d["ppm"] = -ppm if m.group(2) == "slow" else ppm
        m = re.search(r"RMS offset\s*:\s*([\d.]+)\s*seconds", ch)
        if m:
            d["jitter_ms"] = float(m.group(1)) * 1000.0
            d["jitter_source"] = "chrony-rms-offset"
        src = "\n".join(sections.get("CHRONY-SOURCES", []))
        m = re.search(r"^\^\*\s+(\S+)", src, re.M)
        if m:
            d["server"] = m.group(1)
    return d


def score_ms(ppm, jitter_ms, offset_ms):
    """Accuracy score in expected milliseconds of disagreement.

    Defined in DESIGN.md: drift one clock accumulates over one Exocortical
    Epoch quantum (420 s) at its measured frequency error, plus the noise
    in its knowledge of its own offset (jitter), plus one tenth of its
    current offset snapshot (the daemon is already slewing that away; it
    is the least durable of the three numbers). Lower is better.
    """
    return 0.42 * abs(ppm) + jitter_ms + 0.1 * abs(offset_ms)


def fmt(v, nd=3):
    if v is None:
        return "none"
    return f"{v:.{nd}f}"


def probe_node(name, ip):
    cmd = ["ssh", "-i", SSH_KEY, "-o", "IdentitiesOnly=yes",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
           "-o", "BatchMode=yes",
           "-o", "ProxyCommand=nc -X connect -x hatch-egress-proxy:3130 %h %p",
           f"wbic16@{ip}", "bash", "-s"]
    try:
        r = subprocess.run(cmd, input=REMOTE, capture_output=True, text=True,
                           timeout=40)
    except subprocess.TimeoutExpired:
        return {"node": name, "ip": ip, "reachable": False,
                "reason": "ssh-timeout"}
    if r.returncode != 0 or "===END===" not in r.stdout:
        err = (r.stderr or "").strip().splitlines()
        reason = err[-1][:80] if err else f"ssh-exit-{r.returncode}"
        return {"node": name, "ip": ip, "reachable": False, "reason": reason}
    d = parse_probe(r.stdout)
    d.update({"node": name, "ip": ip, "reachable": True})
    d["eligible"] = bool(d["synced"] and d["ppm"] is not None
                         and d["jitter_ms"] is not None
                         and d["offset_ms"] is not None)
    if d["eligible"]:
        d["score_ms"] = score_ms(d["ppm"], d["jitter_ms"], d["offset_ms"])
    return d


def atom_line(d):
    if not d["reachable"]:
        return (f"node {d['node']} ip={d['ip']} reachable=no "
                f"reason={d.get('reason', 'unknown')}")
    return ("node {node} ip={ip} reachable=yes engine={engine} synced={synced} "
            "server={server} ppm={ppm} offset_ms={off} jitter_ms={jit} "
            "jitter_source={jsrc} score_ms={score} eligible={elig}").format(
        node=d["node"], ip=d["ip"], engine=d["engine"] or "none",
        synced="yes" if d["synced"] else "no", server=d["server"] or "none",
        ppm=fmt(d["ppm"]), off=fmt(d["offset_ms"]), jit=fmt(d["jitter_ms"]),
        jsrc=d["jitter_source"] or "none", score=fmt(d.get("score_ms")),
        elig="yes" if d["eligible"] else "no")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleet", default="fleet.txt")
    ap.add_argument("--register", default="register.txt")
    args = ap.parse_args()

    fleet = []
    with open(args.fleet) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                name, ip = line.split()
                fleet.append((name, ip))

    now = int(time.time())
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    lines = [f"census {stamp} ee={ee_of(now)}"]
    results = []
    for i, (name, ip) in enumerate(fleet):
        if i:
            time.sleep(1)  # human pace between probes
        d = probe_node(name, ip)
        results.append(d)
        lines.append(atom_line(d))
        print(lines[-1], flush=True)
    # Nodes known to the fleet record but unmeasurable from here.
    lines.append("node great-white ip=none reachable=no "
                 "reason=no-address-in-record-and-key-refused")
    lines.append("end-census")
    with open(args.register, "a") as f:
        f.write("\n".join(lines) + "\n")
    ok = sum(1 for d in results if d["reachable"])
    el = sum(1 for d in results if d.get("eligible"))
    print(f"census complete: {ok}/{len(fleet)} reachable, {el} eligible",
          file=sys.stderr)


if __name__ == "__main__":
    main()
